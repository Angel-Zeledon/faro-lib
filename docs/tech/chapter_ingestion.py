"""Technical manual — how data gets in and what is done to it before modelling.

Written against the code on 2026-09-14. Every `path:line` was read, not
remembered. Where a parameter exists but is never used, this says so: an
engineer who finds it themselves and was not warned stops trusting the rest.
"""

CHAPTER = {
    "id": "ingestion",
    "es": {
        "title": "2. Ingesta: del archivo del cliente a una serie modelable",
        "intro": (
            "Faro no pide un formato. Lee el export que el cliente ya tiene — CSV o "
            "Excel, con separador y codificación variables, encabezados en español y "
            "fechas dd/mm/yyyy — y lo convierte en una tabla canónica. Esta es la "
            "parte que más trabajo de campo tiene y la que más barato es subestimar: "
            "casi todo archivo real llega roto de alguna forma."
        ),
        "topics": [
            {
                "name": "Lectura del archivo",
                "where": "forecasting_core/data/loader.py:109",
                "what": (
                    "Un solo punto de entrada para CSV, XLSX, XLS, Parquet, JSON y SQL. "
                    "Lo que parece trivial es donde se pierden los archivos reales."
                ),
                "how": [
                    "El separador se olfatea del encabezado entre coma, punto y coma, tabulador y barra vertical (loader.py:17).",
                    "Las codificaciones se prueban en orden: utf-8-sig, cp1252, latin-1 (loader.py:37). El BOM de Excel y el cp1252 de Windows son la norma, no la excepción.",
                    "Una columna de fechas inequívocamente dd/mm/yyyy se lee con día primero (loader.py:62). Si es ambigua, no adivina.",
                ],
                "caveats": [
                    "Un archivo con NUL se rechaza en backend/dataframes/io.py, no acá: pandas trunca la celda en el NUL sin avisar, y un truncado silencioso es peor que un rechazo.",
                ],
            },
            {
                "name": "Esquema canónico",
                "where": "forecasting_core/data/canonical.py:49",
                "what": (
                    "Catorce campos canónicos a los que se mapean las columnas del "
                    "usuario. Tres son obligatorios: sku, date, demand. El resto tiene "
                    "un valor por defecto que se difunde a toda la columna cuando el "
                    "archivo no lo trae."
                ),
                "table": [
                    ("sku, date, demand", "Obligatorios. Sin ellos no hay serie."),
                    ("store", "Por defecto «Tienda única». La llave de serie es sku│store, con U+2502 de separador (canonical.py:66)."),
                    ("region", "Por defecto «Sin región»."),
                    ("inventory", "Por defecto 0. Si el archivo la trae de verdad, activa la recuperación de demanda censurada."),
                    ("lead_time", "Por defecto 15 días (DEFAULT_LEAD_TIME_DAYS, canonical.py:23). Es un SUPUESTO, y el producto lo dice en pantalla."),
                    ("price, cost, regular_price, promo_price", "Por defecto None."),
                    ("promo, promo_type, discount", "Por defecto False, «Sin promoción», 0.0."),
                ],
                "caveats": [
                    "Los 15 días de lead time por defecto son el supuesto más caro del sistema: un importador con 45 días de tránsito reordena un mes tarde y nada falla visiblemente.",
                ],
            },
            {
                "name": "Relleno de huecos y valores atípicos",
                "where": "forecasting_core/validation/auto_correct.py:100",
                "what": (
                    "Dos correcciones automáticas antes de modelar, y una distinción "
                    "que importa: rellenar un TARGET nulo no es lo mismo que rellenar "
                    "una FECHA faltante."
                ),
                "how": [
                    "Target nulo: por grupo, ffill → bfill → fillna(0) (auto_correct.py:126). Siempre activo.",
                    "Atípicos: recorte por SKU a [Q1 − 3·IQR, Q3 + 3·IQR] (auto_correct.py:139). El pipeline lo pasa como clip_outliers=True incondicionalmente (pipeline.py:279).",
                    "Fechas faltantes: NO las rellena el motor. Las estrategias (cero, ffill, interpolar, dejar) viven en el catálogo de la compuerta (data/gate.py:107) y las aplica backend/workers/runner.py; el valor por defecto es «dejar».",
                ],
                "caveats": [
                    "auto_correct.py:104 declara un parámetro fill_missing_dates y NUNCA lo usa. Es código muerto: quien lo lea y suponga que rellena fechas se equivoca.",
                    "DataQualityChecker.clip_outliers (quality.py:223) existe y el pipeline no lo llama. El mismo factor 3.0 sí se usa para CONTAR atípicos en el puntaje de calidad.",
                ],
            },
            {
                "name": "Demanda censurada",
                "where": "forecasting_core/data/censoring.py",
                "what": (
                    "Un día sin ventas porque no había producto en bodega no es un día "
                    "de demanda cero. Si el cliente mapeó una columna real de "
                    "inventario, esas observaciones se levantan; si no la mapeó, este "
                    "paso es un no-op."
                ),
                "table": [
                    ("LEVEL_WINDOW", "28 — ventana para estimar el nivel de demanda no censurada."),
                    ("MIN_UNCENSORED", "14 — mínimo de observaciones sin censura para estimar."),
                    ("MAX_LIFT_FACTOR", "3.0 — techo del ajuste. Nunca multiplica una venta por más de tres."),
                    ("demand_censored", "Columna bandera que queda en la tabla."),
                ],
                "caveats": [
                    "Las observaciones solo se LEVANTAN, nunca se bajan. Es una corrección de sesgo hacia arriba y hay que saberlo al leer un pronóstico.",
                ],
            },
            {
                "name": "Clasificación de series",
                "where": "forecasting_core/data/quality.py:80",
                "what": (
                    "Cada SKU recibe etiquetas — es MULTI-ETIQUETA, una serie puede ser "
                    "estacional y volátil a la vez. De esto depende qué modelos se le "
                    "entrenan."
                ),
                "how": [
                    "Si n < min_history (20) → {short} y se corta ahí, sin evaluar nada más (quality.py:101).",
                    "Si la proporción de ceros ≥ 0.4 → intermittent.",
                    "Si CV = std/(|media|+1e-8) ≥ 1.5 → volatile.",
                    "Si hay largo suficiente, STL con period=7: fuerza = max(0, 1 − var(resid)/(var(seasonal+resid)+1e-8)); > 0.3 → seasonal. Cualquier excepción se traga y no marca estacional.",
                    "Sin ninguna bandera → stable.",
                ],
                "formulas": [
                    ("Fuerza estacional", "strength = max(0, 1 - var(resid) / (var(seasonal + resid) + 1e-8))",
                     "STL robusto, período 7 por defecto (training.seasonal_period)."),
                    ("Puntaje de calidad", "100 - 30·(n<20) - min(missing·2, 20) - min(outliers·3, 15) - min(zero_ratio·20, 20)",
                     "Piso en 0, publicado dividido entre 100 (quality.py:266)."),
                ],
                "caveats": [
                    "filter_valid_skus descarta todo SKU con menos de 20 observaciones. Un catálogo nuevo puede perder la mitad de sus productos y eso es correcto, pero hay que explicárselo al cliente.",
                    "Existe una función legado classify_series (quality.py:53) con umbrales propios que el pipeline NO usa.",
                ],
            },
            {
                "name": "Granularidad y agregación",
                "where": "forecasting_core/data/resampler.py:19",
                "what": (
                    "La estrategia por defecto es «native»: no toca nada. Con "
                    "«aggregate» se remuestrea a una frecuencia objetivo sumando el "
                    "target y agregando las demás columnas según un diccionario (el "
                    "inventario se lleva con min)."
                ),
                "caveats": [
                    "Limitación declarada en el propio código (pipeline.py:181): al remuestrear solo se usa la llave de grupo PRIMARIA, así que una configuración con dos llaves suma a través de la secundaria.",
                    "Hay dos escaleras distintas de detección de frecuencia con cotas distintas — profiler.py:911 y profiler.py:935 — más infer_freq_days en quality.py:27. No están unificadas.",
                ],
            },
        ],
    },
    "en": {
        "title": "2. Ingestion: from the customer's file to a modellable series",
        "intro": (
            "Faro does not ask for a format. It reads the export the customer already "
            "has — CSV or Excel, with varying separators and encodings, Spanish "
            "headers and dd/mm/yyyy dates — and turns it into a canonical table. This "
            "is the part with the most field work in it and the cheapest to "
            "underestimate: almost every real file arrives broken in some way."
        ),
        "topics": [
            {
                "name": "Reading the file",
                "where": "forecasting_core/data/loader.py:109",
                "what": (
                    "One entry point for CSV, XLSX, XLS, Parquet, JSON and SQL. What "
                    "looks trivial is where real files get lost."
                ),
                "how": [
                    "The separator is sniffed from the header among comma, semicolon, tab and pipe (loader.py:17).",
                    "Encodings are tried in order: utf-8-sig, cp1252, latin-1 (loader.py:37). Excel's BOM and Windows cp1252 are the norm, not the exception.",
                    "A date column that is unambiguously dd/mm/yyyy is parsed day-first (loader.py:62). Ambiguous ones are not guessed.",
                ],
                "caveats": [
                    "A file containing a NUL byte is refused in backend/dataframes/io.py, not here: pandas truncates the cell at the NUL without a word, and a silent truncation is worse than a refusal.",
                ],
            },
            {
                "name": "Canonical schema",
                "where": "forecasting_core/data/canonical.py:49",
                "what": (
                    "Fourteen canonical fields the user's columns are mapped onto. "
                    "Three are required: sku, date, demand. The rest carry a default "
                    "that is broadcast across the column when the file lacks it."
                ),
                "table": [
                    ("sku, date, demand", "Required. Without them there is no series."),
                    ("store", "Defaults to \"Tienda única\". The series key is sku│store, separator U+2502 (canonical.py:66)."),
                    ("region", "Defaults to \"Sin región\"."),
                    ("inventory", "Defaults to 0. When the file really carries it, censored-demand recovery switches on."),
                    ("lead_time", "Defaults to 15 days (DEFAULT_LEAD_TIME_DAYS, canonical.py:23). It is an ASSUMPTION, and the product says so on screen."),
                    ("price, cost, regular_price, promo_price", "Default to None."),
                    ("promo, promo_type, discount", "Default to False, \"Sin promoción\", 0.0."),
                ],
                "caveats": [
                    "The 15-day default lead time is the most expensive assumption in the system: an importer with 45 days of transit reorders a month late and nothing visibly fails.",
                ],
            },
            {
                "name": "Gap filling and outliers",
                "where": "forecasting_core/validation/auto_correct.py:100",
                "what": (
                    "Two automatic corrections before modelling, and one distinction "
                    "that matters: filling a null TARGET is not the same as filling a "
                    "missing DATE."
                ),
                "how": [
                    "Null target: per group, ffill → bfill → fillna(0) (auto_correct.py:126). Always on.",
                    "Outliers: per-SKU clip to [Q1 − 3·IQR, Q3 + 3·IQR] (auto_correct.py:139). The pipeline passes clip_outliers=True unconditionally (pipeline.py:279).",
                    "Missing dates: the engine does NOT fill them. The strategies (zero, forward fill, interpolate, leave) live in the gate catalogue (data/gate.py:107) and are applied by backend/workers/runner.py; the default is \"leave\".",
                ],
                "caveats": [
                    "auto_correct.py:104 declares a fill_missing_dates parameter and NEVER uses it. It is dead code: anybody reading it and assuming dates get filled is wrong.",
                    "DataQualityChecker.clip_outliers (quality.py:223) exists and the pipeline never calls it. The same 3.0 factor IS used to COUNT outliers for the quality score.",
                ],
            },
            {
                "name": "Censored demand",
                "where": "forecasting_core/data/censoring.py",
                "what": (
                    "A day with no sales because there was nothing on the shelf is not "
                    "a day of zero demand. If the customer mapped a real inventory "
                    "column, those observations are lifted; if they did not, this step "
                    "is a no-op."
                ),
                "table": [
                    ("LEVEL_WINDOW", "28 — window used to estimate the uncensored demand level."),
                    ("MIN_UNCENSORED", "14 — minimum uncensored observations before estimating."),
                    ("MAX_LIFT_FACTOR", "3.0 — ceiling on the adjustment. It never multiplies a sale by more than three."),
                    ("demand_censored", "Flag column left on the table."),
                ],
                "caveats": [
                    "Observations are only ever RAISED, never lowered. It is an upward bias correction and you have to know that when reading a forecast.",
                ],
            },
            {
                "name": "Series classification",
                "where": "forecasting_core/data/quality.py:80",
                "what": (
                    "Every SKU gets labels — it is MULTI-LABEL, a series can be "
                    "seasonal and volatile at once. Which models get trained for it "
                    "depends on this."
                ),
                "how": [
                    "If n < min_history (20) → {short}, returning immediately without evaluating anything else (quality.py:101).",
                    "If the zero ratio ≥ 0.4 → intermittent.",
                    "If CV = std/(|mean|+1e-8) ≥ 1.5 → volatile.",
                    "If long enough, STL with period=7: strength = max(0, 1 − var(resid)/(var(seasonal+resid)+1e-8)); > 0.3 → seasonal. Any exception is swallowed and the seasonal flag is simply not set.",
                    "No flag set → stable.",
                ],
                "formulas": [
                    ("Seasonal strength", "strength = max(0, 1 - var(resid) / (var(seasonal + resid) + 1e-8))",
                     "Robust STL, period 7 by default (training.seasonal_period)."),
                    ("Quality score", "100 - 30·(n<20) - min(missing·2, 20) - min(outliers·3, 15) - min(zero_ratio·20, 20)",
                     "Floored at 0, published divided by 100 (quality.py:266)."),
                ],
                "caveats": [
                    "filter_valid_skus drops every SKU with fewer than 20 observations. A new catalogue can lose half its products, which is correct behaviour but has to be explained to the customer.",
                    "A legacy classify_series (quality.py:53) exists with its own thresholds and the pipeline does NOT use it.",
                ],
            },
            {
                "name": "Granularity and aggregation",
                "where": "forecasting_core/data/resampler.py:19",
                "what": (
                    "The default strategy is \"native\": it touches nothing. With "
                    "\"aggregate\" the series is resampled to a target frequency, "
                    "summing the target and aggregating other columns per a dict "
                    "(inventory is carried with min)."
                ),
                "caveats": [
                    "A limitation the code states itself (pipeline.py:181): resampling uses only the PRIMARY group key, so a two-key configuration sums across the secondary one.",
                    "There are two different frequency-detection ladders with different bounds — profiler.py:911 and profiler.py:935 — plus infer_freq_days in quality.py:27. They are not unified.",
                ],
            },
        ],
    },
}

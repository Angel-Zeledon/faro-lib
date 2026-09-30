"""Technical manual — what the thing is, before any detail.

The first chapter answers the question an engineer actually opens the document
with: what am I inheriting, what decides what, and what will bite me.
"""

CHAPTER = {
    "id": "overview",
    "es": {
        "title": "1. Qué es esto y cómo está partido",
        "intro": (
            "StockAI convierte el historial de ventas de un distribuidor en la decisión "
            "de compra del día: qué pedir, cuánto, a quién y cuándo hay que ponerlo "
            "para que llegue antes de quedarse sin producto. Este documento describe "
            "cómo lo hace por dentro. Está escrito contra el código, con archivo y "
            "línea, y cuando un número es un supuesto lo dice."
        ),
        "topics": [
            {
                "name": "La cadena completa",
                "what": (
                    "Todo el producto es una cadena de cinco eslabones. Cada uno "
                    "recibe algo medido y entrega algo más útil, y en cada empalme hay "
                    "un supuesto que conviene conocer."
                ),
                "how": [
                    "Historial de ventas (CSV/Excel/SQL) → una tabla canónica con sku, fecha y demanda.",
                    "Por SKU: clasificación de la serie, selección de modelos, entrenamiento con validación hacia adelante, elección de campeón.",
                    "Pronóstico con intervalo → demanda diaria esperada durante el plazo de entrega.",
                    "Contra el stock de hoy: días de cobertura → señal del semáforo → cantidad recomendada.",
                    "Orden de compra, envío al proveedor, recepción y aprendizaje del plazo real.",
                ],
                "caveats": [
                    "El último eslabón es el que cierra el círculo: sin registrar la orden y su recepción, el plazo real del proveedor nunca se aprende y el sistema sigue planificando sobre lo configurado.",
                ],
            },
            {
                "name": "Las tres capas",
                "where": "ForecastingCore/ · backend/ · Frontend/",
                "what": (
                    "Separación estricta y con una razón: el motor puede venderse, "
                    "probarse y reemplazarse sin tocar el producto."
                ),
                "table": [
                    ("ForecastingCore/", "TODA la inteligencia de pronóstico. No conoce la base de datos, no conoce tenants. Recibe un diccionario de configuración y devuelve diccionarios y DataFrames."),
                    ("backend/", "API FastAPI multi-tenant. Orquestación pura: no hace ML, y no puede importar pandas ni numpy fuera de tres módulos — lo verifica un test."),
                    ("Frontend/", "Next.js 14. Sin lógica de negocio: pide, muestra y traduce."),
                ],
                "caveats": [
                    "El motor NUNCA escribe en base de datos. Quien persiste es backend/workers/runner.py, que es también el único puente entre las dos capas.",
                ],
            },
            {
                "name": "Qué decide qué",
                "what": (
                    "Cuatro decisiones mandan sobre todo lo demás. Si algo del "
                    "producto sorprende, casi siempre es una de estas."
                ),
                "table": [
                    ("La clasificación de la serie", "Decide qué modelos se entrenan. Una serie corta (< 20 observaciones) se descarta del todo."),
                    ("El campeón por SKU", "Decide qué pronóstico se usa río abajo. La regla vive en una constante compartida entre el motor y el backend, literalmente, porque cuando divergieron las dos capas discreparon en 8 de 13 productos."),
                    ("El plazo de entrega", "Decide los tres umbrales del semáforo Y la demanda del período de reposición. Es el número más sensible del sistema y su valor por defecto es un supuesto de 15 días."),
                    ("El nivel de servicio", "Decide el colchón. Por defecto 0.95, resoluble por SKU, proveedor, categoría o global."),
                ],
            },
            {
                "name": "Cómo leer el resto del documento",
                "what": (
                    "Cada tema trae lo mismo: qué es, cómo funciona, la fórmula donde "
                    "la hay, y — la sección que más importa — qué NO significa."
                ),
                "caveats": [
                    "Las advertencias no son autocrítica decorativa. Son las cosas que un ingeniero encuentra en la primera semana; encontrarlas sin aviso es lo que hace que el resto del documento parezca publicidad.",
                    "Donde una constante está fija en el código y no es configurable, se dice. Donde un parámetro existe pero nadie lo lee, también.",
                ],
            },
        ],
    },
    "en": {
        "title": "1. What this is, and how it is split",
        "intro": (
            "StockAI turns a distributor's sales history into the purchase decision of "
            "the day: what to order, how much, from whom, and when it has to be placed "
            "so it arrives before the stock runs out. This document describes how it "
            "does that inside. It is written against the code, with file and line, and "
            "where a number is an assumption it says so."
        ),
        "topics": [
            {
                "name": "The whole chain",
                "what": (
                    "The entire product is a chain of five links. Each takes something "
                    "measured and returns something more useful, and at every joint "
                    "there is an assumption worth knowing."
                ),
                "how": [
                    "Sales history (CSV/Excel/SQL) → a canonical table with sku, date and demand.",
                    "Per SKU: series classification, model selection, walk-forward training, champion selection.",
                    "Forecast with an interval → expected daily demand across the lead time.",
                    "Against today's stock: coverage days → stock signal → recommended quantity.",
                    "Purchase order, sent to the supplier, received back, and the real lead time learned.",
                ],
                "caveats": [
                    "The last link is what closes the loop: without recording the order and its reception, the supplier's real lead time is never learned and the system keeps planning on what was configured.",
                ],
            },
            {
                "name": "The three layers",
                "where": "ForecastingCore/ · backend/ · Frontend/",
                "what": (
                    "A strict separation with a reason: the engine can be sold, tested "
                    "and replaced without touching the product."
                ),
                "table": [
                    ("ForecastingCore/", "ALL forecasting intelligence. It does not know the database and does not know tenants. It takes a config dict and returns dicts and DataFrames."),
                    ("backend/", "Multi-tenant FastAPI API. Pure orchestration: no ML, and it may not import pandas or numpy outside three modules — a test enforces it."),
                    ("Frontend/", "Next.js 14. No business logic: it asks, shows and translates."),
                ],
                "caveats": [
                    "The engine NEVER writes to a database. What persists is backend/workers/runner.py, which is also the only bridge between the two layers.",
                ],
            },
            {
                "name": "What decides what",
                "what": (
                    "Four decisions govern everything else. When the product surprises "
                    "you, it is almost always one of these."
                ),
                "table": [
                    ("The series classification", "Decides which models get trained. A short series (< 20 observations) is dropped entirely."),
                    ("The per-SKU champion", "Decides which forecast is used downstream. The rule lives in a constant shared verbatim between the engine and the backend, because when they drifted the two layers disagreed on 8 of 13 products."),
                    ("The lead time", "Decides all three signal thresholds AND the replenishment-period demand. It is the most sensitive number in the system, and its default is an assumption of 15 days."),
                    ("The service level", "Decides the cushion. Default 0.95, resolvable per SKU, supplier, category or globally."),
                ],
            },
            {
                "name": "How to read the rest of this",
                "what": (
                    "Every topic carries the same four things: what it is, how it "
                    "works, the formula where there is one, and — the section that "
                    "matters most — what it does NOT mean."
                ),
                "caveats": [
                    "The warnings are not decorative self-criticism. They are the things an engineer finds in the first week; finding them unannounced is what makes the rest of a document read as marketing.",
                    "Where a constant is hard-coded and not configurable, it says so. Where a parameter exists and nothing reads it, it says that too.",
                ],
            },
        ],
    },
}

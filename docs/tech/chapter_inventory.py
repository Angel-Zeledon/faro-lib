"""Technical manual — how a forecast becomes a purchase decision.

Written against the code on 2026-09-14. Several entries here are warnings
rather than descriptions: this layer carries approximations the code itself
documents as wrong-but-useful, and an engineer who inherits it without being
told will read a number as a measurement.
"""

CHAPTER = {
    "id": "inventory",
    "es": {
        "title": "4. Inventario: del pronóstico a la orden de compra",
        "intro": (
            "Acá vive el producto. El pronóstico es un insumo; lo que el cliente "
            "compra es «pide 78 unidades de esto hoy». Todo este capítulo son cuatro "
            "números encadenados — cobertura, señal, punto de reorden y cantidad — y "
            "la parte más importante es dónde cada uno deja de ser una medición y "
            "pasa a ser un supuesto."
        ),
        "topics": [
            {
                "name": "Días de cobertura",
                "where": "backend/inventory/service.py:1662 · coverage_days",
                "what": (
                    "Cuánto dura el stock que hay hoy al ritmo de venta pronosticado. "
                    "Es el número del que cuelga todo lo demás."
                ),
                "formulas": [
                    ("Cobertura", "coverage_days = current_stock / avg_daily   si avg_daily > 0, si no 9999.0",
                     "`avg_daily` es la media de los primeros `steps` puntos del pronóstico del MEJOR modelo del SKU, con steps = ceil(lead_time_días / días_por_período)."),
                ],
                "caveats": [
                    "NO ESTÁ EN DÍAS, pese al nombre. Está en períodos del grano de planificación activo: en una sesión semanal, «3» son 3 semanas. El campo autoritativo es `coverage_unit`, que viaja al lado.",
                    "El numerador es solo stock en mano. Las órdenes en tránsito NO entran acá (sí entran en la cantidad recomendada), así que un SKU con un camión en camino puede mostrar PEDIR_YA con cantidad 0.",
                    "Demanda cero da el centinela 9999, que clasifica como SOBRESTOCK y se anula antes de salir: el cliente ve `coverage_days: null, signal: SOBRESTOCK`.",
                    "Si el mejor modelo no guardó puntos, cae silenciosamente a promediar TODOS los modelos entrenados.",
                ],
            },
            {
                "name": "El semáforo",
                "where": "backend/inventory/service.py:1065 · _calc_signal",
                "what": "Toda la regla, literal, son cuatro líneas.",
                "formulas": [
                    ("Señal",
                     "cobertura < lead_time·0.5 → PEDIR_YA\ncobertura < lead_time·1.2 → PEDIR_PRONTO\ncobertura < lead_time·3   → OK\nsi no                     → SOBRESTOCK",
                     "Los multiplicadores 0.5 / 1.2 / 3 son literales en el código."),
                ],
                "caveats": [
                    "NO hay configuración de umbrales por tenant ni por SKU. Lo único que varía por producto es el lead time.",
                    "SIN_DATOS no sale de esta función: es la rama cuando falta el pronóstico o falta la fila de stock. En la vista por bodega también aparece cuando la participación de demanda de esa bodega es 0.",
                    "La importación fuerza lead_time_days >= 1: un 0 colapsa los tres umbrales y pinta todo SOBRESTOCK.",
                    "`min_stock` se guarda, se importa y se devuelve, pero NO lo lee ni la señal ni la recomendación. Es peso muerto en esta capa.",
                ],
            },
            {
                "name": "Cantidad recomendada",
                "where": "backend/inventory/service.py:1119 · _calc_recommended",
                "what": (
                    "Demanda del lead time más stock de seguridad, menos lo que ya "
                    "tienes y lo que viene en camino. Con un piso de MOQ y una "
                    "compuerta por señal."
                ),
                "formulas": [
                    ("Cantidad",
                     "raw = max(0, avg_daily·lead_time + safety_stock − current_stock − incoming)\nsi moq > 0 y raw > 0:  raw = max(ceil(raw), moq)",
                     "Después se fuerza a 0 salvo que la señal sea PEDIR_YA o PEDIR_PRONTO."),
                    ("Punto de reorden", "reorder_point = avg_daily·lead_time + safety_stock",
                     "Se publica, pero NO es el disparador: el disparador es la razón de cobertura de arriba."),
                    ("Stock de seguridad — medido (preferido)",
                     "offsets acumulados del backtest de origen rodante, horizonte ceil(lead_time), cuantil más cercano al nivel de servicio",
                     "Solo se usa si el modelo que produjo el riesgo es el mismo campeón del SKU; si no, se descarta."),
                    ("Stock de seguridad — respaldo clásico", "safety = z · σ · sqrt(lead_time)",
                     "σ se extrae del intervalo: sigma = (q90 − valor) / 1.2816."),
                ],
                "caveats": [
                    "El propio código dice que z·σ·√L está mal: supone errores normales e independientes por período, y «ninguna de las dos cosas se cumple — la demanda es no negativa y sesgada, y un pronóstico que va alto hoy va alto mañana, así que los errores se acumulan más rápido que √L». La rama medida existe justamente para no usarla.",
                    "El MOQ es un MÍNIMO, no un múltiplo: una necesidad de 520 con MOQ 500 pide 520, no 1000. Antes pedía 1000.",
                    "NO hay período de revisión. El modelo es de revisión continua, con el disparador reemplazado por la razón de cobertura.",
                    "Los cuatro niveles de servicio históricos (0.90, 0.95, 0.97, 0.99) devuelven z fijado a 3 decimales; cualquier otro nivel se calcula con la aproximación de Acklam. Es una discontinuidad deliberada de ~4e-4 — los cuatro valores se dejan clavados para que las cifras de un tenant existente no se muevan por debajo de él. No es un error de la aproximación: es el precio de no reescribir el pasado.",
                    "Ojo con leer el archivo: _Z[0.90] = 1.282 y _Q90_Z = 1.2816 son la MISMA constante redondeada distinto, en el mismo módulo. La primera arma el colchón; la segunda despeja σ del intervalo del pronóstico. No son intercambiables por casualidad, son iguales a propósito.",
                    "El punto de reorden y la señal pueden contradecirse: cuando el stock de seguridad supera el 20% de la demanda del lead time, un SKU puede estar por DEBAJO de su punto de reorden publicado y aun así pintarse OK con cantidad 0.",
                ],
            },
            {
                "name": "Lead time aprendido",
                "where": "backend/inventory/service.py:1217 · get_learned_lead_times",
                "what": (
                    "Cada recepción completa de una orden deja una observación del "
                    "plazo real del proveedor. Con tres observaciones, el promedio "
                    "aprendido reemplaza a lo configurado."
                ),
                "formulas": [
                    ("Aprendizaje", "AVG(lead_time_days) GROUP BY LOWER(supplier) HAVING COUNT(*) >= 3",
                     "MIN_LEAD_TIME_OBSERVATIONS = 3. Por debajo, el proveedor ni aparece en el mapa."),
                ],
                "caveats": [
                    "Lo aprendido GANA incondicionalmente sobre todo lo configurado, y aplica a TODOS los SKUs de ese proveedor, incluso los configurados a mano uno por uno.",
                    "Es una media simple sobre todas las observaciones de la historia: sin ponderación por recencia, sin rechazo de atípicos, sin techo.",
                    "Solo escribe observación una recepción COMPLETA. Una parcial no aporta nada. Y en una orden multi-proveedor, TODOS los proveedores quedan fechados por la finalización de la orden entera.",
                ],
            },
            {
                "name": "Multi-bodega y traslados",
                "where": "backend/inventory/service.py:1852 · get_inventory_status_by_warehouse",
                "what": (
                    "La demanda se reparte entre bodegas de dos formas, y cuando una "
                    "bodega necesita lo que a otra le sobra, se propone un traslado en "
                    "vez de una compra."
                ),
                "how": [
                    "Modo «store»: la sesión se entrenó con columna de tienda, así que cada bodega tiene su propio pronóstico real. Participación 1.0.",
                    "Modo «share»: el pronóstico global del SKU se multiplica por la participación configurada de cada bodega. Si ninguna tiene participación, el 100% va a la bodega por defecto.",
                    "Un donante debe quedar con al menos 30 días de cobertura propia y poder cubrir al menos el 80% de la necesidad.",
                    "El carril se acepta si es estrictamente más rápido que comprar Y estrictamente más barato; sin costo unitario conocido se acepta solo por tiempo, y se dice.",
                ],
                "caveats": [
                    "El estado agregado («Todas») NO es la suma de las señales por bodega: es un cálculo independiente sobre stock sumado contra el pronóstico sin repartir. Las dos vistas pueden discrepar legítimamente en un SKU.",
                    "Un carril sin configurar vale 1 día, costo 0 y fijo 0 — el propio módulo lo llama «deliberadamente optimista». Le gana a casi cualquier compra. La bandera `lane_is_default` viaja para que la pantalla lo diga.",
                ],
            },
            {
                "name": "El optimizador",
                "where": "ForecastingCore/forecasting_core/business/optimizer.py:2 · MILP inventory optimizer",
                "what": (
                    "Un MILP resuelto con HiGHS (scipy.optimize.milp) que reparte "
                    "compras y traslados en el horizonte, minimizando costo total."
                ),
                "formulas": [
                    ("Objetivo",
                     "min Σ holding·inv + stockout·short + order_cost·order + transfer_cost·transfer + fixed·ship",
                     "Variables por bucket: order, transfer, inv, short (enteras ≥ 0) y ship (binaria, solo en carriles con costo fijo)."),
                    ("Costos", "holding = unit_cost · 0.20 / 365 · días_por_bucket ;  stockout = order_cost · 3.0",
                     "0.20 es el porcentaje anual de bodegaje por defecto; 3.0 el multiplicador de quiebre."),
                ],
                "caveats": [
                    "Límite de 10 segundos de solver, y TODO solve corre en un único hilo dedicado: entrar a HiGHS desde hilos distintos produce un bloqueo que un mutex no arregla.",
                    "Ante excepción, timeout o más de 5000 variables, degrada a un respaldo ingenuo que IGNORA los traslados por completo, y lo reporta como status «fallback».",
                    "Solo un solve concurrente: el segundo recibe 503 y reintenta. Con dos, dos compradores recibían dos planes distintos del mismo catálogo.",
                    "Un costo unitario de 0 o negativo se trata como ausente y se usa 1.0, marcando la línea como `assumed_unit_cost`. El `total_cost` puede ser un número sobre nada.",
                    "El MOQ se aplica DESPUÉS del solve, repartiendo el faltante a la línea más grande. El MILP no tiene variable de pedido mínimo.",
                ],
            },
            {
                "name": "Lo que las cifras de negocio no dicen",
                "where": "backend/inventory/roi_service.py:516",
                "what": (
                    "La pantalla de impacto es deliberadamente más pobre de lo que "
                    "podría ser, y eso es una decisión, no una carencia."
                ),
                "caveats": [
                    "`capital_freed` es la diferencia entre dos fotos mensuales de sobrestock. El sobrestock también baja por vender, por merma, por borrar productos y por reentrenar: la cifra NO es atribuible a Faro y el código lo dice.",
                    "No se calcula ningún titular de «Faro te ahorró $X» ni un conteo de «quiebres evitados», porque ambos necesitan supuestos que no están fundados en los datos del cliente.",
                    "El stock muerto es una heurística: un SKU está «muerto» cuando su consumo observado es menor al 20% del esperado por pronóstico, con al menos 2 fotos y sin reposición en la ventana.",
                    "El ABC usa demanda diaria × costo unitario como aproximación de ingreso, cayendo a costo 1.0 cuando no hay costo. Los cortes XYZ son CV 0.5 y 1.0, fijos en el código.",
                ],
            },
        ],
    },
    "en": {
        "title": "4. Inventory: from a forecast to a purchase order",
        "intro": (
            "This is where the product lives. The forecast is an input; what the "
            "customer buys is \"order 78 of these today\". This whole chapter is four "
            "chained numbers — coverage, signal, reorder point, quantity — and the "
            "most important part is where each one stops being a measurement and "
            "becomes an assumption."
        ),
        "topics": [
            {
                "name": "Coverage days",
                "where": "backend/inventory/service.py:1662 · coverage_days",
                "what": (
                    "How long today's stock lasts at the forecast sales rate. Every "
                    "other number hangs off this one."
                ),
                "formulas": [
                    ("Coverage", "coverage_days = current_stock / avg_daily   if avg_daily > 0, else 9999.0",
                     "`avg_daily` is the mean of the first `steps` forecast points of the SKU's BEST model, with steps = ceil(lead_time_days / days_per_period)."),
                ],
                "caveats": [
                    "IT IS NOT IN DAYS, despite the name. It is in periods of the active planning grain: on a weekly session, \"3\" means 3 weeks. The authoritative field is `coverage_unit`, shipped alongside.",
                    "The numerator is on-hand stock only. Orders in transit do NOT enter here (they do enter the recommended quantity), so a SKU with a truck inbound can show PEDIR_YA with quantity 0.",
                    "Zero demand yields the 9999 sentinel, which classifies as SOBRESTOCK and is nulled before leaving: the customer sees `coverage_days: null, signal: SOBRESTOCK`.",
                    "If the best model stored no points, it silently falls back to averaging EVERY trained model.",
                ],
            },
            {
                "name": "The stock signal",
                "where": "backend/inventory/service.py:1065 · _calc_signal",
                "what": "The whole rule, verbatim, is four lines.",
                "formulas": [
                    ("Signal",
                     "coverage < lead_time·0.5 → PEDIR_YA\ncoverage < lead_time·1.2 → PEDIR_PRONTO\ncoverage < lead_time·3   → OK\notherwise                → SOBRESTOCK",
                     "The 0.5 / 1.2 / 3 multipliers are literals in that function."),
                ],
                "caveats": [
                    "There is NO per-tenant and NO per-SKU threshold configuration. The only thing that varies per product is the lead time.",
                    "SIN_DATOS does not come out of this function: it is the branch taken when the forecast is missing or the stock row is. In the per-warehouse view it also fires when that warehouse's demand share is 0.",
                    "Import forces lead_time_days >= 1: a stray 0 collapses all three thresholds and paints everything SOBRESTOCK.",
                    "`min_stock` is stored, imported and returned, but is read by NEITHER the signal NOR the recommendation. It is dead weight in this layer.",
                ],
            },
            {
                "name": "Recommended quantity",
                "where": "backend/inventory/service.py:1119 · _calc_recommended",
                "what": (
                    "Lead-time demand plus safety stock, minus what you have and what "
                    "is already coming. With an MOQ floor and a gate on the signal."
                ),
                "formulas": [
                    ("Quantity",
                     "raw = max(0, avg_daily·lead_time + safety_stock − current_stock − incoming)\nif moq > 0 and raw > 0:  raw = max(ceil(raw), moq)",
                     "It is then forced to 0 unless the signal is PEDIR_YA or PEDIR_PRONTO."),
                    ("Reorder point", "reorder_point = avg_daily·lead_time + safety_stock",
                     "It is published, but it is NOT the trigger: the trigger is the coverage ratio above."),
                    ("Safety stock — measured (preferred)",
                     "cumulative offsets from the rolling-origin backtest, horizon ceil(lead_time), quantile nearest the service level",
                     "Used only when the model that produced the risk is the SKU's champion; otherwise discarded."),
                    ("Safety stock — classical fallback", "safety = z · σ · sqrt(lead_time)",
                     "σ is extracted from the interval: sigma = (q90 − value) / 1.2816."),
                ],
                "caveats": [
                    "The code itself says z·σ·√L is wrong: it assumes per-bucket errors are normal and independent, and \"neither holds: demand is non-negative and skewed, and a forecast that runs high today runs high tomorrow, so the errors compound faster than √L\". The measured branch exists precisely to avoid it.",
                    "MOQ is a MINIMUM, not a multiple: a need of 520 with MOQ 500 orders 520, not 1000. It used to order 1000.",
                    "There is NO review period. The model is continuous-review, with the trigger replaced by the coverage ratio.",
                    "The four historical service levels (0.90, 0.95, 0.97, 0.99) return a z pinned to 3 decimals; every other level is computed with the Acklam approximation. That is a deliberate ~4e-4 discontinuity — the four values are held fixed so an existing tenant's numbers do not shift underneath them. It is not approximation error: it is the price of not rewriting the past.",
                    "Careful reading the file: _Z[0.90] = 1.282 and _Q90_Z = 1.2816 are the SAME constant rounded differently, in the same module. The first builds the cushion; the second backs σ out of the forecast interval. They are not interchangeable by accident, they are equal on purpose.",
                    "The reorder point and the signal can contradict each other: when safety stock exceeds 20% of lead-time demand, a SKU can sit BELOW its own published reorder point and still be painted OK with quantity 0.",
                ],
            },
            {
                "name": "Learned lead time",
                "where": "backend/inventory/service.py:1217 · get_learned_lead_times",
                "what": (
                    "Every completed reception leaves an observation of the supplier's "
                    "real lead time. At three observations, the learned average "
                    "replaces what was configured."
                ),
                "formulas": [
                    ("Learning", "AVG(lead_time_days) GROUP BY LOWER(supplier) HAVING COUNT(*) >= 3",
                     "MIN_LEAD_TIME_OBSERVATIONS = 3. Below it the supplier is absent from the map entirely."),
                ],
                "caveats": [
                    "The learned value WINS unconditionally over everything configured, and applies to EVERY SKU of that supplier — including ones configured by hand, one at a time.",
                    "It is a plain mean over every observation ever recorded: no recency weighting, no outlier rejection, no cap.",
                    "Only a COMPLETE reception writes an observation. A partial one contributes nothing. And on a multi-supplier order, EVERY supplier is dated by the completion of the whole order.",
                ],
            },
            {
                "name": "Multi-warehouse and transfers",
                "where": "backend/inventory/service.py:1852 · get_inventory_status_by_warehouse",
                "what": (
                    "Demand is split across warehouses in one of two ways, and when one "
                    "warehouse needs what another has spare, a transfer is proposed "
                    "instead of a purchase."
                ),
                "how": [
                    "\"store\" mode: the session was trained with a store column, so each warehouse has its own real forecast. Share 1.0.",
                    "\"share\" mode: the SKU-global forecast is multiplied by each warehouse's configured share. If none has one, 100% of demand goes to the default warehouse.",
                    "A donor must keep at least 30 days of its own coverage and be able to cover at least 80% of the need.",
                    "A lane is accepted if it is strictly faster than purchasing AND strictly cheaper; with no known unit cost it is accepted on time alone, and says so.",
                ],
                "caveats": [
                    "The aggregate status (\"All\") is NOT a rollup of the per-warehouse signals: it is an independent computation on summed stock against the un-split forecast. The two views can legitimately disagree on a SKU.",
                    "An unconfigured lane is worth 1 day, cost 0 and fixed 0 — the module calls it \"deliberately optimistic\" itself. It beats almost any purchase. The `lane_is_default` flag rides along so the screen can say so.",
                ],
            },
            {
                "name": "The optimizer",
                "where": "ForecastingCore/forecasting_core/business/optimizer.py:2 · MILP inventory optimizer",
                "what": (
                    "A MILP solved with HiGHS (scipy.optimize.milp) that spreads "
                    "purchases and transfers across the horizon, minimising total cost."
                ),
                "formulas": [
                    ("Objective",
                     "min Σ holding·inv + stockout·short + order_cost·order + transfer_cost·transfer + fixed·ship",
                     "Variables per bucket: order, transfer, inv, short (integer ≥ 0) and ship (binary, only on lanes with a fixed cost)."),
                    ("Costs", "holding = unit_cost · 0.20 / 365 · days_per_bucket ;  stockout = order_cost · 3.0",
                     "0.20 is the default annual holding rate; 3.0 the stockout multiplier."),
                ],
                "caveats": [
                    "A 10-second solver limit, and EVERY solve runs on one dedicated thread: entering HiGHS from different OS threads deadlocks, and a mutex does not fix it.",
                    "On exception, timeout, or more than 5000 variables, it degrades to a naive fallback that IGNORES transfers entirely, reported as status \"fallback\".",
                    "One concurrent solve only: a second caller gets a 503 and retries. At two, two buyers received two different plans for the same catalogue.",
                    "A unit cost of 0 or negative is treated as missing and 1.0 is used, flagging the line as `assumed_unit_cost`. The `total_cost` can be a number about nothing.",
                    "MOQ is applied AFTER the solve, adding the shortfall to the largest line. The MILP has no minimum-order variable.",
                ],
            },
            {
                "name": "What the business figures do not say",
                "where": "backend/inventory/roi_service.py:516",
                "what": (
                    "The impact screen is deliberately poorer than it could be, and "
                    "that is a decision rather than a gap."
                ),
                "caveats": [
                    "`capital_freed` is the difference between two monthly overstock snapshots. Overstock also falls on sales, shrinkage, SKU deletion and retraining: the figure is NOT attributable to Faro, and the code says so.",
                    "No \"Faro saved you $X\" headline and no \"stockouts avoided\" count are computed, because both need assumptions not grounded in the tenant's own data.",
                    "Dead stock is a heuristic: a SKU is \"dead\" when observed depletion is under 20% of forecast-expected depletion, with at least 2 snapshots and no restocking in the window.",
                    "ABC uses daily demand × unit cost as a revenue proxy, falling back to a unit cost of 1.0 when none is on file. The XYZ cutoffs are CV 0.5 and 1.0, hard-coded.",
                ],
            },
        ],
    },
}

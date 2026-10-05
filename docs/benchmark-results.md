# Benchmark de precisión del motor (2026-10-05)

Este documento dice, con evidencia, qué tan bien pronostica el motor de StockAI frente a métodos de referencia simples. Los números son los que salieron; no se retocó nada. Donde el motor pierde, se dice.

## Cómo se midió

- **Qué se probó:** el punto de entrada público del motor, el mismo que usa `backend/workers/runner.py`: `ForecastEngine.from_dict(config)` -> `load_data` -> `train` -> `get_metrics` / `get_forecast`. La configuración replica los valores por defecto del asistente (validación walk-forward de 3 cortes, `min_history` 20, 7 funciones de calendario activas).
- **Qué pronóstico se evalúa:** el del *campeón* de cada serie, elegido igual que el producto (menor `cost_horizon` entre los modelos que no son baselines). Además se reportan por separado cada modelo y el `ensemble` que el motor también produce.
- **Holdout:** rolling origin con 2 orígenes consecutivos. En cada origen el motor entrena solo con datos anteriores y se evalúa el horizonte siguiente (8 semanas en sintético, 18 meses en M4 mensual, 13 semanas en M4 semanal).
- **Referencias:** `naive`, `seasonal_naive`, `moving_average` (ventana 4). En el sintético hay además `oracle_true_mean`: el pronóstico que usaría alguien que conoce la media verdadera del generador.
- **Métricas:** MASE (error absoluto medio / error del seasonal naive dentro de la muestra), WAPE (suma de errores absolutos / suma de demanda real, agrupado), sMAPE, sesgo (suma de pronóstico menos real, sobre demanda real; negativo = se queda corto), pinball escalado por la misma escala de MASE (promedio de los cuantiles que entrega el motor) y cobertura del intervalo 80 % (q10-q90). **FVA vs naive** = 1 - (error absoluto del método / error absoluto del naive), agrupado por segmento; positivo es mejor que naive.
- **Segmentos:** cuadrantes de Syntetos-Boylan calculados sobre la ventana de entrenamiento (ADI 1,32 y CV² 0,49): smooth, erratic, intermittent, lumpy.
- **Todos los métodos se comparan sobre las mismas series-origen** (aquellas donde el motor produjo pronóstico; el motor falló en 0 de ellas en las tres corridas).

## Reproducir

```
cd ForecastingCore
python -m benchmarks.run --dataset synthetic --max-series 90 --origins 2 --chunk 15 --time-budget-min 14
python -m benchmarks.run --dataset m4_monthly --max-series 60 --origins 2 --chunk 15 --time-budget-min 14
python -m benchmarks.run --dataset m4_weekly  --max-series 60 --origins 2 --chunk 15 --time-budget-min 14
python -m pytest benchmarks/tests -q      # pruebas de métricas y de determinismo
```

Semilla 42 en todo. Salidas (JSON con el detalle por serie y tabla markdown): `ForecastingCore/benchmarks/results/`. El comando es `benchmarks.run` y no `forecasting_core.benchmarks.run` porque el encargo era añadir archivos solo bajo `ForecastingCore/benchmarks/`.

Versiones: Python 3.12.10, numpy 2.4.6, pandas 3.0.3, lightgbm 4.6.0, xgboost 3.2.0, statsmodels 0.14.6, forecasting_core 1.0.0.

## Limitaciones que hay que conocer antes de citar un número

1. **Muestra pequeña, y menor a la pedida.** Cada corrida tenía un tope de 14 minutos; se detuvo por tiempo antes de completar las series solicitadas. Series realmente evaluadas: sintético 60 de 90, M4 mensual 45 de 60, M4 semanal 30 de 60 (2 orígenes cada una, así que 120 / 90 / 60 series-origen). Las series se procesan en orden aleatorio con semilla, así que lo evaluado sigue siendo una muestra aleatoria, pero con intervalos de confianza amplios. Los segmentos con 19-21 series-origen (erratic, intermittent, lumpy) son **indicativos, no concluyentes**.
2. **Los modelos pesados no se corrieron.** Solo LightGBM, XGBoost, ETS, ARIMA y Croston. Quedaron fuera Prophet, LSTM, SARIMAX y `global_lgbm` por la regla de recursos. Si alguno de ellos gana en producción, este benchmark no lo ve.
3. **ARIMA y Croston se ven solo en las series a las que el router los asigna** (p. ej. ARIMA en 31 de 90 series-origen en M4 mensual). Sus filas no son comparables directamente con las de los demás modelos: se promedian sobre otro subconjunto.
4. **M4 no tiene segmentos intermitentes** (todas sus series son positivas y suaves), así que M4 solo habla del segmento *smooth*. Las fechas de M4 son sintéticas (el conjunto no trae fechas) y se usaron las últimas 144 (mensual) / 260 (semanal) observaciones.
5. **WAPE y MASE penalizan distinto en demanda intermitente.** Pronosticar cero reduce el error absoluto, de modo que el naive y hasta la media verdadera (`oracle_true_mean`) pierden contra un pronóstico bajo. Por eso en intermitente/lumpy el oráculo aparece peor que el naive: no es un error de la medición, es que el error absoluto no premia la media. Hay que leer esos segmentos junto con el sesgo y el pinball.
6. **Los datos sintéticos son un generador propio** (suave, estacional, errático, intermitente, lumpy, con promociones). Sirven por tener verdad conocida, no demuestran nada sobre sus datos reales.
7. **Los tiempos de ejecución están inflados:** por un error mío se solaparon dos cadenas de corridas durante un rato, así que el reloj no es una medición limpia del costo del motor. Las métricas de precisión no se ven afectadas.
8. Los promos del generador **no se pasan** al motor como variable exógena (el runner del producto tampoco las pasa), así que esa serie no mide el efecto de usar la promoción como dato.

## Resultados

### Sintético (60 series, 2 orígenes, horizonte 8 semanas, 1021 s)

Todas las series-origen (n=120):

| método | MASE | WAPE | sesgo | FVA vs naive | victorias/derrotas vs naive |
|---|---|---|---|---|---|
| **motor (campeón)** | 0,858 | 22,8 % | -4,0 % | **+31,5 %** | 54/46 |
| ensemble | 0,819 | 22,1 % | -4,3 % | +33,7 % | 57/43 |
| ETS solo | 0,823 | 22,1 % | -4,6 % | +33,6 % | 60/40 |
| LightGBM solo | 0,851 | 23,1 % | -5,0 % | +30,7 % | 54/46 |
| XGBoost solo | 0,879 | 24,3 % | -3,3 % | +27,1 % | 47/53 |
| moving_average | 1,036 | 25,2 % | -3,2 % | +24,3 % | 55/46 |
| seasonal_naive | 1,062 | 28,9 % | -0,3 % | +13,2 % | 49/64 |
| naive | 1,287 | 33,3 % | +1,6 % | referencia | - |
| oráculo (media verdadera) | 0,779 | 17,4 % | +0,4 % | +47,9 % | - |

Por segmento, motor contra lo mejor entre los baselines:

| segmento (n) | WAPE motor | FVA motor vs naive | mejor baseline (WAPE) | lectura |
|---|---|---|---|---|
| smooth (60) | 14,8 % | +27,7 % | moving_average 16,6 % | el motor gana; el oráculo llega a 7,7 %, queda margen |
| erratic (20) | 80,2 % | +36,8 % | moving_average 83,4 % | gana por poco; ETS solo (77,5 %) lo supera |
| intermittent (21) | 129,3 % | +3,2 % | naive 133,5 % | **empate práctico con naive**; MASE 0,781 contra 0,791 |
| lumpy (19) | 101,8 % | +39,1 % | seasonal_naive 156,2 % | gana, pero MASE 1,104 y sesgo -91 %: en la práctica pronostica casi cero |

Cobertura del intervalo 80 %: **76,6 % global** (smooth 69,0 %, erratic 91,2 %, intermittent 83,3 %, lumpy 77,6 %). Es decir, las bandas son algo estrechas en demanda suave.

Campeón elegido: LightGBM 57, ETS 29, XGBoost 29, ARIMA 4, Croston 1.

Donde el motor es peor que una alternativa disponible: en el agregado y en smooth, el `ensemble` y ETS solo le ganan al campeón elegido (MASE 0,819 / 0,823 contra 0,858). En erratic ETS solo gana. En lumpy, ARIMA tiene MASE 0,677 contra 1,104, pero sobre un subconjunto de series y con WAPE de 125,8 %, así que no es una victoria clara.

### M4 mensual (45 series, 2 orígenes, horizonte 18 meses, 1225 s)

Todas son *smooth*. n=90 series-origen.

| método | MASE | WAPE | sesgo | FVA vs naive | cobertura 80 % |
|---|---|---|---|---|---|
| **motor (campeón)** | 0,878 | 10,3 % | +1,7 % | **+10,8 %** | **60,1 %** |
| ensemble | 0,870 | 9,7 % | +1,4 % | +15,6 % | - |
| ETS solo | 0,917 | 8,8 % | +2,5 % | +21,3 % | - |
| LightGBM solo | 1,142 | 11,7 % | +1,4 % | -0,9 % | - |
| XGBoost solo | 1,131 | 11,4 % | +0,3 % | +1,1 % | - |
| moving_average | 1,001 | 10,7 % | -0,2 % | +7,3 % | - |
| naive | 1,091 | 11,5 % | +1,1 % | referencia | - |
| seasonal_naive | 1,164 | 12,6 % | +0,3 % | -9,0 % | - |

Aquí el motor **gana a los baselines pero por un margen modesto**, y pierde contra sus propios componentes: ETS solo y el ensemble tienen menor WAPE. LightGBM y XGBoost, que el motor elige como campeón en 32 de 90 casos, no le ganan ni al naive en esta muestra. **La cobertura del intervalo 80 % es de solo 60,1 %**: la banda es demasiado estrecha a 18 pasos. Campeón: ETS 45, LightGBM 17, XGBoost 15, ARIMA 13.

### M4 semanal (30 series, 2 orígenes, horizonte 13 semanas, 895 s)

Todas *smooth*. n=60 series-origen.

| método | MASE | WAPE | sesgo | FVA vs naive | cobertura 80 % |
|---|---|---|---|---|---|
| **motor (campeón)** | 0,464 | 4,0 % | +0,5 % | **+37,7 %** | **68,1 %** |
| ensemble | 0,458 | 3,7 % | -0,2 % | +42,7 % | - |
| ETS solo | 0,387 | 4,9 % | -0,8 % | +47,6 % | - |
| LightGBM solo | 0,541 | 4,5 % | -0,7 % | +30,2 % | - |
| XGBoost solo | 0,585 | 4,8 % | -0,5 % | +25,7 % | - |
| moving_average | 0,723 | 6,6 % | -2,4 % | -1,4 % | - |
| naive | 0,712 | 6,5 % | -1,7 % | referencia | - |
| seasonal_naive | 0,981 | 11,1 % | -7,4 % | -71,9 % | - |

Gana claramente a los baselines. De nuevo ETS solo (por MASE) y el ensemble superan al campeón elegido. (ARIMA aparece con WAPE 1,9 % pero solo sobre 24 series-origen donde el router lo usó; no es comparable.)

## Qué diría con honestidad el dueño hoy

- Sobre series suaves o estacionales el motor supera a naive en 11 % a 38 % de error absoluto, y a seasonal naive de forma más holgada. Es una mejora real y repetida en los tres conjuntos.
- **No** se puede afirmar que gane en demanda intermitente: es un empate con naive en esta muestra (n=21).
- **No** se puede afirmar que el campeón sea la mejor elección entre los modelos disponibles: en los tres conjuntos el `ensemble` o ETS solo tuvieron igual o mejor error que el campeón elegido.
- Los intervalos del 80 % cubren entre 60 % y 77 % en la práctica: están sobreconfiados, sobre todo a horizontes largos.

## Qué movería los números (ordenado de más débil a más fuerte)

Sin implementar nada; son candidatos para decidir.

1. **Intervalos de predicción sobreconfiados (cobertura 60-77 % contra 80 % nominal; M4 mensual 60 %).** Candidato: calibrar los cuantiles con residuos de validación walk-forward por horizonte (conformal) en lugar de ancho constante basado en la desviación residual. Afecta directamente el stock de seguridad.
2. **Demanda intermitente (FVA +3 % vs naive, n=21).** Candidato: enrutar estas series a Croston/TSB o ETS por defecto (ya ganan al campeón en esta muestra: MASE 0,718 y 0,715 contra 0,781) y, más importante, evaluar con una pérdida adecuada a la intermitencia (pinball / costo asimétrico) en lugar de MAE, porque con MAE la mejor decisión es pronosticar cero.
3. **Lumpy (sesgo -91 %).** El motor pronostica prácticamente cero. Candidato: para el stock, usar un cuantil alto de la distribución de demanda en lugar del punto; considerar un modelo de tamaño y frecuencia por separado.
4. **Selección del campeón (ensemble y ETS solo igualan o superan al campeón en los tres conjuntos).** Candidato: considerar el ensemble como candidato en la carrera de campeones, o combinar por defecto cuando las diferencias entre modelos sean pequeñas; el campeón por serie con pocas ventanas de validación parece sobreajustar a una ventana.
5. **LightGBM/XGBoost en M4 mensual (no mejoran a naive).** Candidato: restringir los modelos globales de árboles a series con historia suficiente o con variables exógenas, y para series largas y suaves dejar los estadísticos como valor por defecto.
6. **Series suaves con margen (oráculo 7,7 % contra motor 14,8 %).** Parte del hueco es ruido irreducible, pero indica que hay algo de ganancia posible en estacionalidad anual con poca historia (156 semanas = 3 ciclos).

Para ampliar la evidencia: repetir con 300 series por conjunto y sin presión de memoria, añadir otras semillas, y correr Prophet / `global_lgbm` / LSTM, que aquí no se evaluaron.

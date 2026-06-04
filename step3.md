Actúa como un arquitecto senior de machine learning y software especializado en forecasting industrial multi-SKU (retail, supply chain, demanda) y diseño de plataformas tipo SaaS de ML.

Tengo un sistema de forecasting ya implementado en Python con arquitectura modular que incluye:

- Configuración en JSON por sesiones (multi-experimento)
- Modelos: LightGBM, XGBoost, ARIMA, Prophet y LSTM
- Feature engineering con lags, rolling, ewm y diffs
- Pipeline básico de entrenamiento por SKU
- Evaluación simple con MAE, RMSE y WAPE
- Loader de datos desde CSV / Excel / SQL
- Separación básica de módulos (loader, features, models, trainer, evaluation)

Quiero que analices este sistema y lo lleves conceptualmente a nivel producción enterprise-grade, como si fuera un sistema tipo Amazon Forecast, Walmart demand planning o Meta-scale time series platform.

---

# OBJETIVO

Quiero un análisis técnico profundo, estructurado y crítico sobre todo lo que falta para que este sistema sea:

> una plataforma real de forecasting multi-tenant, multi-SKU, robusta y usable por cualquier usuario (incluso no técnico)

---

# DEBES INCLUIR OBLIGATORIAMENTE

## 1. VALIDACIÓN TEMPORAL CORRECTA (CRÍTICO)
- Walk-forward validation
- Expanding window
- Backtesting multi-horizon real
- Evitar leakage en cualquier capa del pipeline
- Consistencia entre train / inference

---

## 2. DATA LAYER (INGESTA Y CALIDAD) (CRÍTICO)
- Data onboarding automático (usuarios no técnicos)
- Data validation por SKU
- Detección de schema automático (date, target, id)
- Missing dates handling
- Outliers detection
- Minimum history per series
- Intermittency detection (series con muchos ceros)
- Data quality scoring por dataset

---

## 3. FEATURE ENGINEERING AVANZADO (CRÍTICO)
- Feature store (persistente y reutilizable)
- Feature versioning
- Prevención formal de leakage
- Features adaptativas según frecuencia de datos
- Variables exógenas (precio, promociones, stock, calendario)
- Consistencia total entre training e inference

---

## 4. MODELING LAYER (CRÍTICO)
- Comparación justa entre modelos estadísticos y ML
- Model routing automático según tipo de serie:
  - estable
  - estacional
  - intermitente
  - corta
  - compleja
- Manejo correcto de series intermitentes
- Diseño correcto de LSTM (sequences, scaling, windows)
- Prophet con regresores exógenos
- ARIMA vs SARIMAX correctamente aplicado
- Fallback models

---

## 5. EVALUATION LAYER (CRÍTICO)
- Métricas por horizonte (MAE@h, WAPE@h)
- Métricas por SKU y agregación ponderada
- Baselines obligatorios (naive, seasonal naive)
- Business loss functions (cost-based forecasting)
- Forecast intervals (P50, P90, P95)
- Evaluación robusta tipo backtesting

---

## 6. ENSEMBLES (IMPORTANTE)
- Weighted ensembles por SKU
- Dynamic weighting según performance histórica
- Stacking simple entre modelos

---

## 7. MODEL MANAGEMENT (CRÍTICO)
- Model registry completo
- Experiment tracking
- Config versioning con hash
- Reproducibilidad total de runs
- Comparación entre sesiones

---

## 8. MONITORING EN PRODUCCIÓN (CRÍTICO)
- Data drift detection
- Prediction drift
- Feature drift
- Performance decay tracking
- Alerting automático

---

## 9. SCALABILITY (IMPORTANTE)
- Batch processing eficiente
- Parallelization por SKU
- Optimización de memoria
- Posible distribución de carga

---

## 10. PRODUCTIZATION (CRÍTICO)
- API layer en FastAPI
- CRUD de sesiones
- Upload de datasets (CSV, Excel, SQL)
- Endpoint de training
- Endpoint de prediction
- Endpoint de metrics
- Frontend HTML para control de sesiones
- Pipeline completamente configurable por JSON
- Logging y auditoría de todo el sistema

---

# CONTEXTO ADICIONAL (MUY IMPORTANTE)

Debes asumir que el objetivo final es:

> “cualquier usuario puede subir datos y obtener forecasts sin conocimiento técnico”

Por lo tanto debes incluir análisis sobre:

- onboarding automático de datasets
- detección de columnas (date, target, SKU)
- validación de calidad antes de entrenar
- robustez del sistema ante datos arbitrarios
- fallback behavior cuando el dataset es malo o incompleto

---

# TAMBIÉN DEBES INCLUIR

- Errores típicos que ya estoy cometiendo aunque no los haya mencionado
- Riesgos de diseño actuales
- Fallos comunes en sistemas similares en industria
- Cuellos de botella futuros

---

# ARQUITECTURA FINAL

Debes proponer una arquitectura en capas clara:

- UI Layer (frontend)
- API Layer (FastAPI)
- Orchestration / Pipeline Engine
- Feature Store Layer
- Model Layer
- Evaluation Layer
- Monitoring Layer
- Storage Layer

---

# FORMATO DE RESPUESTA

Debes responder como si estuvieras diseñando un sistema real para una empresa global de retail o supply chain.

- No superficial
- No genérico
- Técnico, estructurado y crítico
- Enfocado en producción real

Además debes clasificar cada punto como:
- CRÍTICO
- IMPORTANTE
- OPCIONAL


  Demo credentials:
  - demo@acmecorp.demo / Demo2024! — admin
  - analyst@acmecorp.demo / Demo2024! — analyst
  - viewer@acmecorp.demo / Demo2024! — viewer
  - demo2@distribuidora.demo / Demo2024! — admin


en la config del modelo, no peude pegar con configuracion de la data que choque, tiene que tener sentido y restringirse usar transformaciones en data y meterle esa data a modelos que no la usan asi, al menos dar la advertencia, como modelos como prophet al final van a ignorar otras columnas opcionales que el usuario usara
en el dashboard, al darle view all debe ver la lista de sesiones divididas por paginas de 10, no ir a data.
en data al cargar un archivo, size, rows,columns, type deben actualizarse inmediatamente se procesa el archivo

en data al ver el analisis, las variables que se muestran, deberian poder especificarse el rango de fechas, puede ser pues el dataset completo y ver todos el analisis de eso o elegir rango de fechas

signo de pregunta par ver el significado del concepto del input a la par de cada input de la pagina forecast, para que al configurar la sesion la persona sepa exactamente que significa ese input

reports tiene que ir a workspace, y donde esta ai analyst, debe cambiarse a agent ai, y debe haber una pagina nueva llamada agent files, donde se pueden subir pdfs, words, txt que se deben subir a pineconce y el chat puedo responder en base a ellos. deben poder eliminarse, subirse archivos, una barra de busqueda para filtrar por nombre de archivo, debe salir hora de subida, tipo de archivo, y deben representarse con tarjetas con icono segun el tipo de archivo y cuando lo abra se abra un visor del archivo. en el chat se va a poder filtrar segun la session y segun los archivos que quieras, debe sair una ventana emergente con una columna sessions y otra agent fles y cambia la lista segun cual tengo seleccionado y ya luego le doy a un boton de save. recuerda que los archivos de data deben poder ser consultados por el agente rag

quiero que crees varios csv de prueba con distintos tipos de data, principalmente para probar los metodos de data faltante, de gaps de ceros, etc, si en algun sku intelligence se decide usar algun metodo para rellenar gaps, debe reflejarse en la grafica de algun otro color e indicando que tecnica se uso para rellenar esas fechas. en dsku intelligence puede tambien descargarse un pdf que sera una imagen de la grafica segun el theme en el que este. en dicha grafica debe poder hacerse zoom vertical y horizotal para cambiar el tamano de la grafica, a mas largo a lo vertical, mas grande en el eje y es la grafica y asi. 

en toda pagina no debe haber scroll horizontal a menos que sea un dataset o una grafica, si es una lista de algo debe caber justamente en su contenedor y mostrar toda la informacion, si hay que hacer mas grande ese contenedor o disminuir la letra se hace.

en la pagina data, no debe haber en la lista de data, dos botones de file y sql db, xq ya deporsi al abrir la pagina salen en el div grande de la derecha, el cual se debe mostrar siempre que se le de al boton de new item, el cual reemplazara a los 3 botones que salen bajo el input para buscar y arriba de la lista. verifica que esa pagina siga el theme de la pagina xq creo que no lo sigue. 

en forecast al cargar las variables que no son target, sku o date, las que se le aplican normalizacion o onehot segun es categorica o numerica, pero no salen todas, aveces salen solo las categoricas o aveces solo las numericas, hay que revisar eso.

quiero que en toda pagina si no ha cargado toda la data que debe mostrar, salga la ruedita cargado, pero que no salgan valores ceros o nulos y luego se actualzan, que no cargue la pagina hasta tener todo.

los training results que salen en results en la pagina forecast al terminar la session, deben verse tambien en la pagina reports, exactamente como se ve en esa pagina, no se debe reemplazar el contenido de reports, pero si agregarse eso sobre el "all sessions overview" y debe agregarse al export de el report. toda metrica en report y en results en forecast deben tener maximo 1 decimal. tanto en todos los reportes y descargas que se hagan, lo mismo para las graficas de sku intelligence
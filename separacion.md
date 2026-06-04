````text id="r4e2xk"
Actúa como un principal software architect + ML platform engineer + backend architect especializado en sistemas SaaS enterprise de machine learning y forecasting.

Quiero rediseñar completamente la arquitectura de mi plataforma para que siga principios profesionales de desacoplamiento, mantenibilidad y escalabilidad.

NO quiero una aplicación monolítica donde la lógica de machine learning esté mezclada con endpoints o frontend.

Quiero separar completamente el sistema en 3 aplicaciones independientes:

1. CORE ML LIBRARY (Python package)
2. API LAYER (FastAPI backend)
3. FRONTEND APPLICATION (React o similar)

Quiero que estructures la arquitectura completa bajo esa filosofía.

---

# OBJETIVO PRINCIPAL

Toda la lógica de machine learning, forecasting, data engineering, feature engineering, evaluación, entrenamiento, inferencia y análisis debe vivir EXCLUSIVAMENTE dentro de una librería Python creada por mí.

La API NO debe contener lógica de machine learning.

La API solo debe:
- autenticar usuarios
- manejar sesiones
- gestionar permisos
- recibir requests
- llamar métodos del core
- devolver respuestas

El frontend solo debe:
- configurar visualmente el sistema
- consumir endpoints
- mostrar dashboards y resultados

La lógica real vive únicamente dentro del core ML.

---

# ARQUITECTURA GENERAL OBLIGATORIA

El sistema debe estar dividido en:

```text id="4pw8rw"
Frontend App
    ↓
FastAPI Layer
    ↓
Python ML Core Library
    ↓
Storage / Models / Artifacts
````

---

# 1. CORE ML LIBRARY (SISTEMA PRINCIPAL)

Quiero que diseñes una librería Python enterprise-grade para forecasting y machine learning.

Debe comportarse como una librería real instalable:

```bash
pip install my_forecasting_core
```

---

# RESPONSABILIDADES DEL CORE

TODO lo relacionado con ML debe vivir aquí:

## DATA LAYER

* loaders
* dataframe normalization
* schema detection
* validation
* quality checks
* gaps handling
* outlier detection
* frequency detection
* decomposition
* data profiling

## FEATURE ENGINEERING

* lags
* rolling
* ewm
* calendar features
* onehot encoding
* label encoding
* scaling
* feature store
* feature versioning
* leakage prevention

## MODELING

* LightGBM
* XGBoost
* ARIMA
* SARIMA
* Prophet
* LSTM
* GRU
* CatBoost
* ensembles
* model routing
* model selection

## TRAINING

* walk-forward validation
* backtesting
* hyperparameter tuning
* multi-horizon forecasting
* recursive forecasting

## EVALUATION

* MAE
* RMSE
* WAPE
* weighted metrics
* business metrics
* confidence intervals

## FORECASTING

* inference
* batch forecasting
* future dataframe generation

## DECISION LAYER

* inventory recommendations
* reorder point
* safety stock
* stockout risk
* anomaly detection

## LLM LAYER

* AI analyst
* insight generation
* executive summaries
* anomaly explanations

---

# 2. EL CORE DEBE SER AGNÓSTICO

El core NO debe depender de:

* FastAPI
* frontend
* React
* HTTP
* endpoints

Debe poder usarse directamente desde Python.

Ejemplo:

```python id="h0zmdr"
from forecasting_core import ForecastEngine

engine = ForecastEngine(config)

engine.load_data()

engine.choose_columns()

engine.configure_features()

engine.train()

engine.predict()

engine.generate_report()
```

---

# 3. MUY IMPORTANTE — LA API Y EL FRONTEND DEBEN COMPORTARSE COMO SI FUERAN EL USUARIO PROGRAMANDO LA LIBRERÍA

Quiero que tengas en cuenta que:

* la librería está diseñada para ser usada por código Python
* PERO el frontend/backend deben permitir que usuarios NO técnicos hagan exactamente lo mismo visualmente

Es decir:

Si normalmente alguien haría:

```python id="b6cq0m"
engine.choose_columns(
    target="sales",
    date="date",
    sku="item_id"
)
```

Entonces:

1. el backend debe llamar métodos del core para obtener metadata del dataframe
2. el backend expone eso por endpoints
3. el frontend muestra las columnas disponibles
4. el usuario elige visualmente
5. el frontend manda selección a la API
6. la API llama nuevamente al core con esa configuración

---

# IMPORTANTE

El backend y frontend NO implementan lógica ML.

Solo exponen visualmente las capacidades del core.

Es decir:

> el frontend y backend son una interfaz visual/orquestadora de una librería Python profesional.

---

# 4. CAPA DE INTERACCIÓN CON DATA (MUY IMPORTANTE)

La librería debe tener una capa especial para interactuar dinámicamente con datasets.

Debe permitir:

## DATASET INSPECTION

* listar columnas
* detectar tipos
* detectar fechas
* detectar columnas categóricas
* detectar columnas numéricas
* detectar posibles targets

## DATAFRAME METADATA

* null counts
* cardinality
* distributions
* missing dates
* unique SKUs
* frequency

## TRANSFORMATION CONFIG

* seleccionar columnas
* seleccionar transformaciones
* configurar features

---

# ESTO ES CLAVE

La API y frontend deben consumir estas funciones dinámicamente.

Ejemplo:

```python id="08zqth"
engine.get_available_columns()

engine.get_column_metadata()

engine.get_recommended_transformations()

engine.get_data_quality_report()
```

Luego el frontend convierte eso en:

* tablas
* forms
* dropdowns
* checkboxes
* dashboards

---

# 5. DOCUMENTACIÓN OBLIGATORIA DEL CORE

MUY IMPORTANTE:

Toda la librería debe estar completamente documentada.

Quiero una capa de documentación profesional.

---

# TODO DEBE ESTAR DOCUMENTADO

* clases
* métodos
* parámetros
* retornos
* configuraciones
* schemas
* ejemplos
* flujos internos

---

# QUIERO:

## DOCUMENTACIÓN TIPO SDK

Ejemplo:

```text id="g6f9uh"
docs/
├── engine.txt
├── datasets.txt
├── training.txt
├── forecasting.txt
├── evaluation.txt
├── api_reference.txt
└── examples/
```

---

# ADEMÁS

Cada método importante debe incluir:

* qué hace
* inputs
* outputs
* ejemplos
* errores comunes
* casos de uso

---

# 6. CONFIGURATION SYSTEM

Quiero un sistema de configuración profesional.

Debe existir:

## session_config.json

Con:

* dataset config
* features config
* model config
* validation config
* forecast config
* business rules

---

# IMPORTANTE

Toda configuración debe ser:

* serializable
* reproducible
* validable

Debe existir:

* config validation
* schema validation
* config versioning
* hashing de configuraciones

---

# 7. API LAYER (FASTAPI)

La API debe ser completamente desacoplada del core.

La API NO implementa forecasting.

La API solo:

* recibe requests
* valida permisos
* llama el core
* devuelve resultados

---

# ENDPOINT TYPES

## AUTH

* login
* signup
* refresh token
* password reset
* email verification

## TENANTS

* companies
* users
* roles

## SESSIONS

* create session
* update session
* delete session
* get session

## DATASETS

* upload file
* connect SQL source
* preview dataframe
* inspect metadata
* get available columns

## FORECAST

* train
* predict
* metrics
* reports
* diagnostics

---

# IMPORTANTE

Los endpoints NO deben contener:

* pandas logic
* ML logic
* feature engineering
* forecasting code
* model selection

TODO eso vive en el core.

---

# 8. FRONTEND APPLICATION

El frontend debe ser otra aplicación totalmente separada.

Responsabilidades:

* dashboards
* configuración visual
* tablas
* gráficos
* formularios
* autenticación
* UX guiada

El frontend NO debe:

* ejecutar ML
* tener lógica de forecasting
* manejar modelos directamente

---

# EL FRONTEND DEBE FUNCIONAR COMO:

> una interfaz visual para controlar la librería Python.

---

# 9. INTERNAL CORE ARCHITECTURE

Diseña cómo debería verse el core internamente.

Ejemplo:

```text id="9p7cd8"
core/
├── data/
├── datasets/
├── profiling/
├── diagnostics/
├── features/
├── preprocessing/
├── models/
├── training/
├── forecasting/
├── evaluation/
├── business/
├── inventory/
├── llm/
├── orchestration/
├── registry/
├── artifacts/
├── config/
├── validation/
├── pipelines/
├── monitoring/
├── utils/
└── sdk/
```

Pero quiero una versión MUCHO más profesional y enterprise-grade.

---

# 10. PIPELINE ORCHESTRATION

Quiero que el core tenga un pipeline engine interno.

Ejemplo:

```python id="mk9p2d"
pipeline.run()
```

Y que internamente haga:

```text id="vjlwm8"
load
→ validate
→ clean
→ profile
→ feature engineering
→ train
→ evaluate
→ select best model
→ generate forecasts
→ generate insights
→ export artifacts
```

---

# 11. ARTIFACT MANAGEMENT

El core debe gestionar:

* modelos
* métricas
* plots
* forecasts
* logs
* reports
* feature metadata

Debe existir:

* artifact storage
* model registry
* experiment tracking

---

# 12. ESCALABILIDAD

Quiero que la arquitectura esté diseñada para:

* workers distribuidos
* procesamiento paralelo por SKU
* jobs asíncronos
* colas
* cloud deployment

---

# 13. UX PRINCIPLE IMPORTANTE

La complejidad técnica debe vivir SOLO en el core.

El usuario final solo ve:

* formularios
* dashboards
* configuraciones simples
* insights claros

---

# 14. OBJETIVO FINAL

Quiero que el sistema quede diseñado como:

* una librería Python profesional reutilizable
* una API desacoplada
* un frontend desacoplado
* arquitectura enterprise-grade real

Piensa como si estuvieras diseñando:

* una mezcla entre MLflow
* Databricks
* Amazon Forecast
* un SaaS moderno de analytics

NO quiero código todavía.

Quiero:

* arquitectura
* separación de responsabilidades
* diseño profesional
* decisiones técnicas correctas
* flujo interno correcto
* estructura escalable real

```
```



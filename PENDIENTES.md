# Pendientes — ForecastEngine Platform

Estado al 2026-05-24. Arquitectura base completa (forecasting_core + api + frontend 9 páginas + backend multi-tenant).

## Implementado en esta sesión ✓
- ✅ **Email service** — SMTP Gmail con templates HTML (`Backend/notifications/email.py`)
- ✅ **LLM AI Analyst** — Anthropic claude-opus-4-7, con contexto de sesión + fallback local (`Backend/api/v1/analyst.py`)
- ✅ **Auth frontend** — Login/signup pages, `AuthGuard`, token management (`Frontend/src/app/(auth)/`, `Frontend/src/lib/auth.ts`)
- ✅ **ECharts en /skus** — Tab "Forecast" con gráfico histórico + forecast + CI (`Frontend/src/app/skus/page.tsx`)
- ✅ **Forecast series endpoint** — `GET /sessions/{id}/forecast-series/{sku}` (`Backend/api/v1/forecasts.py`)
- ✅ **API proxy → puerto 8001** — `next.config.ts` apunta al Backend multi-tenant
- ✅ **Logout en Sidebar** — Usuario + rol + botón de logout

---

## 1. ECharts — Visualización de series de tiempo en `/skus`

**Qué falta:** La página SKU Intelligence tiene tabla de métricas pero no el gráfico de series de tiempo con histórico + forecast + intervalos de confianza.

### Backend — nuevo endpoint necesario

```
GET /sessions/{session_id}/forecast/{sku}
```

Respuesta esperada:
```json
{
  "sku": "SKU-001",
  "historical": [
    { "date": "2024-01-01", "value": 142.5 }
  ],
  "forecast": [
    { "date": "2024-07-01", "value": 138.2, "lower": 121.0, "upper": 155.4 }
  ],
  "model": "lightgbm"
}
```

Implementar en `api/routers/forecast.py`:
- Llamar `engine.get_forecast_series(sku)` — método a agregar en `ForecastEngine`
- `ForecastEngine.get_forecast_series(sku)` debe extraer la serie histórica del `_df` y las predicciones del `_forecast_df` (si existe)

### Frontend — componente ECharts

Archivo: `frontend/src/app/skus/page.tsx`

- Instalar: `echarts` + `echarts-for-react` (ya en `package.json`)
- Importar con `dynamic(() => import('echarts-for-react'), { ssr: false })` para evitar error SSR
- Agregar tab **"Forecast Chart"** en el panel de detalle del SKU
- Opción ECharts:
  ```ts
  {
    grid: { top: 20, bottom: 40, left: 50, right: 20 },
    xAxis: { type: 'time', axisLine: { lineStyle: { color: '#1e2030' } } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#1e2030' } } },
    series: [
      { name: 'Historical', type: 'line', data: [...], lineStyle: { color: '#818cf8' } },
      { name: 'Forecast',   type: 'line', data: [...], lineStyle: { color: '#22c55e', type: 'dashed' } },
      { name: 'Upper CI',   type: 'line', data: [...], lineStyle: { opacity: 0 }, stack: 'ci' },
      { name: 'Lower CI',   type: 'line', data: [...], areaStyle: { color: 'rgba(34,197,94,0.08)' }, stack: 'ci' },
    ]
  }
  ```
- Selector de modelo (dropdown) para ver el forecast de cada modelo entrenado

---

## 2. Autenticación Frontend

**Qué falta:** El `backend/` multi-tenant (puerto 8001) tiene JWT + roles + tenants pero el frontend no tiene login/signup ni maneja tokens. Todo va directo al `api/` simple (puerto 8000).

### Páginas a crear

```
frontend/src/app/(auth)/login/page.tsx
frontend/src/app/(auth)/signup/page.tsx
frontend/src/app/(auth)/layout.tsx   ← sin Sidebar, pantalla centrada
```

### Token management

Crear `frontend/src/lib/auth.ts`:
```ts
const TOKEN_KEY = 'fe_token'
const TENANT_KEY = 'fe_tenant'

export function getToken() { return localStorage.getItem(TOKEN_KEY) }
export function getTenant() { return localStorage.getItem(TENANT_KEY) }
export function setAuth(token: string, tenantId: string) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(TENANT_KEY, tenantId)
}
export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(TENANT_KEY)
}
export function isAuthenticated() { return !!getToken() }
```

Actualizar `frontend/src/lib/api.ts`:
- Agregar header `Authorization: Bearer ${getToken()}` en `request()`
- Agregar `X-Tenant-ID` header desde `getTenant()`
- Capturar 401 y redirigir a `/login`

### Flujo de login

```
POST http://localhost:8001/api/v1/auth/login
{ "email": "...", "password": "..." }
→ { "access_token": "...", "tenant_id": "..." }
```

### Protección de rutas

Crear `frontend/src/components/layout/AuthGuard.tsx`:
- Verificar `isAuthenticated()` en cada render
- Redirigir a `/login` si no hay token
- Envolver `AppShell` con `AuthGuard` en `layout.tsx`

### Cambios en Sidebar

- Agregar nombre de usuario y tenant en el footer (actualmente solo muestra "API → localhost:8000")
- Botón de logout que llama `clearAuth()` y redirige a `/login`

---

## 3. LSTM — Implementación completa

**Qué falta:** `forecasting_core/models/lstm.py` existe como stub. No entrena de verdad.

### Implementación

Archivo: `forecasting_core/models/lstm.py`

```python
class LSTMForecaster:
    def __init__(self, config: dict):
        self.lookback    = config.get('lookback', 12)
        self.hidden_size = config.get('hidden_size', 64)
        self.num_layers  = config.get('num_layers', 2)
        self.dropout     = config.get('dropout', 0.2)
        self.epochs      = config.get('epochs', 50)
        self.lr          = config.get('lr', 1e-3)
        self.model       = None
        self.scaler      = None

    def _build_sequences(self, series: np.ndarray):
        X, y = [], []
        for i in range(self.lookback, len(series)):
            X.append(series[i - self.lookback:i])
            y.append(series[i])
        return np.array(X), np.array(y)

    def fit(self, series: pd.Series) -> 'LSTMForecaster':
        # Requiere: torch
        import torch
        import torch.nn as nn
        # ... arquitectura LSTM, training loop, early stopping
        return self

    def predict(self, horizon: int) -> np.ndarray:
        # Autoregressive: usa las últimas `lookback` observaciones,
        # predice una a la vez, agrega al buffer
        ...
```

**Dependencias a agregar en `setup.py` extras `[dl]`:**
```
torch>=2.0.0
```

**Integración en `ModelRouter`:** LSTM se asigna a series de tipo `stable` o `seasonal` con `n_rows >= 50`.

---

## 4. Hyperparameter Optimization

**Qué falta:** Los modelos usan hiperparámetros fijos del config. No hay tuning automático.

### Implementación con Optuna

Archivo nuevo: `forecasting_core/training/tuner.py`

```python
import optuna

class HyperparamTuner:
    def __init__(self, model_name: str, n_trials: int = 30):
        self.model_name = model_name
        self.n_trials   = n_trials

    def tune(self, X_train, y_train, cv_splitter) -> dict:
        def objective(trial):
            params = self._suggest(trial)
            # walk-forward CV con params sugeridos
            scores = []
            for X_tr, X_val, y_tr, y_val in cv_splitter.split(X_train, y_train):
                model = ModelFactory.create(self.model_name, params)
                model.fit(X_tr, y_tr)
                pred = model.predict(X_val)
                scores.append(mae(y_val, pred))
            return np.mean(scores)

        study = optuna.create_study(direction='minimize')
        study.optimize(objective, n_trials=self.n_trials, show_progress_bar=False)
        return study.best_params

    def _suggest(self, trial) -> dict:
        if self.model_name == 'lightgbm':
            return {
                'n_estimators':  trial.suggest_int('n_estimators', 100, 1000),
                'learning_rate': trial.suggest_float('lr', 1e-3, 0.3, log=True),
                'num_leaves':    trial.suggest_int('num_leaves', 20, 200),
                'min_child_samples': trial.suggest_int('min_child_samples', 5, 100),
            }
        # xgboost, etc.
```

**Integración en `Trainer`:** Agregar `tuning: bool` en `TrainingConfig`. Si `True`, correr `HyperparamTuner.tune()` antes del entrenamiento final.

**API endpoint:**
```
POST /sessions/{id}/config/training
{ "tuning": true, "tuning_trials": 30 }
```

**Frontend (`/forecast`, Step 5):** Agregar toggle "Enable hyperparameter tuning" con campo para número de trials.

---

## 5. Drift Monitoring en `/reports`

**Qué falta:** `forecasting_core/monitoring/drift.py` calcula PSI + KS pero no hay endpoint en el API ni visualización en el frontend.

### Backend — nuevo endpoint

```
GET /sessions/{session_id}/drift
```

Requiere que el engine tenga datos de referencia (train) y datos nuevos (pasados en body o desde un segundo archivo).

```python
# En engine.py, nuevo método:
def detect_drift(self, new_data_path: str) -> dict:
    new_df  = DataLoader(new_data_path).load()
    report  = DriftDetector(self._df, new_df).run()
    # report: { sku: { psi: float, ks_stat: float, ks_pval: float, drifted: bool } }
    return report
```

Endpoint:
```python
# routers/forecast.py
@router.post("/{session_id}/drift")
async def detect_drift(session_id: str, file: UploadFile):
    state = require_trained(get_session_or_404(session_id))
    # guardar file temporal, llamar engine.detect_drift(path)
    return state.engine.detect_drift(tmp_path)
```

### Frontend — tab "Drift" en `/reports`

- Upload de nuevo archivo CSV para comparar contra baseline
- Tabla por SKU: PSI score (verde < 0.1, amarillo 0.1–0.25, rojo > 0.25), KS p-value, badge "DRIFT DETECTED" / "STABLE"
- Barra horizontal coloreada por nivel de PSI

---

## 6. LLM real en AI Analyst

**Qué falta:** `/analyst` usa lógica determinística. `engine.ask_ai()` devuelve fallback.

### Opción recomendada: Anthropic API en el backend

Agregar endpoint en `api/routers/meta.py` (o nuevo `routers/analyst.py`):

```python
POST /sessions/{session_id}/ask
{ "question": "Which SKU has the highest stockout risk?" }
→ { "answer": "..." }
```

Implementación:
```python
import anthropic

client = anthropic.Anthropic()  # usa ANTHROPIC_API_KEY del entorno

def ask_analyst(session_id: str, question: str) -> str:
    state   = require_trained(get_session_or_404(session_id))
    context = state.engine.generate_report()  # dict con métricas, inventory, etc.

    message = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        system="You are a demand forecasting analyst. Answer concisely based only on the provided session data.",
        messages=[{
            "role": "user",
            "content": f"Session data:\n{json.dumps(context, indent=2)}\n\nQuestion: {question}"
        }]
    )
    return message.content[0].text
```

**Frontend:** Reemplazar `generateResponse()` en `analyst/page.tsx` por llamada a `POST /sessions/{id}/ask`. Agregar estado de loading real (streaming opcional con SSE).

**Variables de entorno necesarias:**
```
ANTHROPIC_API_KEY=sk-ant-...
```

---

## 7. Email Service real

**Qué falta:** El `backend/` loggea tokens de verificación a consola. No hay envío real.

### Implementación con SendGrid (o SES)

Archivo: `backend/notifications/email.py`

```python
import sendgrid
from sendgrid.helpers.mail import Mail

class EmailService:
    def __init__(self, api_key: str, from_email: str):
        self.sg         = sendgrid.SendGridAPIClient(api_key)
        self.from_email = from_email

    def send_verification(self, to: str, token: str, tenant_name: str):
        link = f"https://yourapp.com/verify?token={token}"
        msg  = Mail(
            from_email=self.from_email,
            to_emails=to,
            subject=f"Verify your {tenant_name} account",
            html_content=f'<a href="{link}">Click here to verify</a>',
        )
        self.sg.send(msg)

    def send_password_reset(self, to: str, token: str):
        ...
```

Integrar en `backend/auth/` reemplazando los `print()` actuales.

**Variables de entorno a agregar en `.env`:**
```
SENDGRID_API_KEY=SG....
FROM_EMAIL=noreply@yourapp.com
```

---

## 8. Migración JSON → PostgreSQL

**Qué falta:** Todo el storage del `backend/` es JSON atómico en disco. No escala.

### Stack recomendado

- **SQLAlchemy 2.x** (async) + **asyncpg**
- **Alembic** para migraciones
- **PostgreSQL 15+**

### Esquema de tablas

```sql
CREATE TABLE tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE NOT NULL,
    plan        TEXT NOT NULL DEFAULT 'free',
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE users (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID REFERENCES tenants(id) ON DELETE CASCADE,
    email        TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'analyst',
    verified     BOOLEAN DEFAULT false,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     UUID REFERENCES users(id),
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'DRAFT',
    config      JSONB,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID REFERENCES sessions(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'queued',
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error       TEXT,
    artifacts   JSONB
);
```

### Plan de migración

1. Agregar `DATABASE_URL` en `.env`
2. Crear `backend/db/base.py` (engine async, session factory)
3. Crear modelos SQLAlchemy espejando las tablas
4. Reescribir `storage/` con repositorios que usen la BD
5. Mantener JSON storage como fallback hasta validar
6. Correr `alembic upgrade head` en deploy

---

## Resumen de esfuerzo estimado

| Item | Complejidad | Dependencias externas |
|------|------------|----------------------|
| ECharts `/skus` | Baja | ninguna |
| Auth frontend | Media | backend/ ya listo |
| LSTM completo | Media | torch |
| Hyperparameter tuning | Media | optuna |
| Drift monitoring | Baja | endpoint nuevo |
| LLM Analyst | Baja | anthropic SDK |
| Email service | Baja | sendgrid |
| PostgreSQL migration | Alta | postgres, alembic |

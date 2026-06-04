# Pendientes — ForecastingCore

Estado al 2026-05-24 — **TODOS LOS ÍTEMS COMPLETADOS**

---

## ✅ Completados

### 1. LSTM — formato correcto + `horizon` + autoregresivo + EarlyStopping
`models/lstm.py` — 2-layer LSTM con `_lstm_recursive_forecast()`. Devuelve `{sku: {"mae", "forecast", "residuals"}}` cuando `horizon > 0`. EarlyStopping con `patience=5` y `restore_best_weights=True`.

### 2. SARIMAX — módulo completo
`models/sarimax.py` — SARIMAX con exógenas, misma interfaz que los demás modelos estadísticos. Integrado en pipeline y router (STABLE/SEASONAL).

### 3. Hyperparameter optimization — `training/tuner.py`
`HyperparamTuner` con Optuna, search spaces para LightGBM y XGBoost. Fallback silencioso si optuna no está instalado. Integrado en `Trainer._maybe_tune()`.

---

## Pendientes (prioridad baja)

### 4. LSTM — backend PyTorch como alternativa
TensorFlow/Keras ya funciona. PyTorch sería un backend alternativo activado por flag `{"backend": "torch"}` en config del modelo. **No bloqueante.**

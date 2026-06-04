"""
LSTM model for forecasting_core.

Requires TensorFlow (optional dependency). Falls back gracefully if not installed.

Returns the same interface as all other stat models:
    {sku: {"mae": float, "forecast": ndarray, "residuals": ndarray}}

When horizon=0 only the evaluation metrics are computed (faster).
"""

import logging
import numpy as np

log = logging.getLogger(__name__)

WINDOW = 14  # default lookback window


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_tf() -> bool:
    try:
        import tensorflow  # noqa: F401
        return True
    except ImportError:
        return False


def _create_sequences(series: np.ndarray, window: int):
    """Convert a 1-D series into (X, y) supervised pairs."""
    X, y = [], []
    for i in range(len(series) - window):
        X.append(series[i: i + window])
        y.append(series[i + window])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def _build_model(window: int):
    """Build a simple 2-layer LSTM → Dense architecture."""
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, Dense, Dropout
    model = Sequential([
        LSTM(64, activation="tanh", return_sequences=True,
             input_shape=(window, 1)),
        Dropout(0.1),
        LSTM(32, activation="tanh"),
        Dense(1),
    ])
    model.compile(optimizer="adam", loss="mse")
    return model


def _lstm_recursive_forecast(
    model,
    scaler,
    last_window_scaled: np.ndarray,
    horizon: int,
) -> np.ndarray:
    """Autoregressive forecast: predict 1 step, shift window, repeat."""
    preds = []
    buf = last_window_scaled.copy()

    for _ in range(horizon):
        X_in = buf.reshape(1, len(buf), 1)
        pred_scaled = float(model.predict(X_in, verbose=0)[0, 0])
        pred_scaled = float(np.clip(pred_scaled, 0.0, 1.0))
        pred_real = float(scaler.inverse_transform([[pred_scaled]])[0, 0])
        pred_real = max(0.0, pred_real)
        preds.append(pred_real)
        buf = np.append(buf[1:], pred_scaled)

    return np.array(preds)


def _lstm_mc_forecast(
    model,
    scaler,
    last_window_scaled: np.ndarray,
    horizon: int,
    n_passes: int = 30,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Monte Carlo Dropout forecast — keeps Dropout active during inference.

    Returns (p10, p50, p90) each of shape (horizon,) in original scale.
    """
    all_runs = []
    for _ in range(n_passes):
        preds = []
        buf = last_window_scaled.copy()
        for _ in range(horizon):
            X_in = buf.reshape(1, len(buf), 1)
            # training=True keeps Dropout active
            pred_scaled = float(model(X_in, training=True).numpy()[0, 0])
            pred_scaled = float(np.clip(pred_scaled, 0.0, 1.0))
            pred_real = float(scaler.inverse_transform([[pred_scaled]])[0, 0])
            pred_real = max(0.0, pred_real)
            preds.append(pred_real)
            buf = np.append(buf[1:], pred_scaled)
        all_runs.append(preds)

    runs = np.array(all_runs)  # shape (n_passes, horizon)
    p10 = np.maximum(0.0, np.percentile(runs, 10, axis=0))
    p50 = np.maximum(0.0, np.percentile(runs, 50, axis=0))
    p90 = np.maximum(0.0, np.percentile(runs, 90, axis=0))
    return p10, p50, p90


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_lstm_core(
    df,
    dt: str,
    target: str,
    group,
    train_ratio: float = 0.8,
    min_rows: int = 30,
    seasonal_period: int = 7,
    horizon: int = 0,
    window: int = WINDOW,
    epochs: int = 50,
    patience: int = 5,
):
    """
    Train an LSTM per SKU and return evaluation metrics (+ future forecast).

    Signature matches other stat model run_*_core functions so it can be
    dropped into the pipeline stat model loop without changes.

    Args:
        df:             Raw DataFrame.
        dt:             Date column name.
        target:         Target column name.
        group:          Group / SKU column name (None = single series).
        train_ratio:    Fraction of data to use for training.
        min_rows:       Minimum rows required (must be > window + 1).
        seasonal_period: Unused — kept for interface parity.
        horizon:        Steps ahead for future forecast (0 = skip).
        window:         Lookback window size.
        epochs:         Max training epochs.
        patience:       EarlyStopping patience.

    Returns:
        {sku: {"mae": float}} when horizon=0
        {sku: {"mae": float, "forecast": ndarray, "residuals": ndarray}} when horizon>0
    """
    if not _check_tf():
        log.warning("TensorFlow not installed — skipping LSTM. Install with: pip install tensorflow")
        return {}

    from sklearn.preprocessing import MinMaxScaler
    from tensorflow.keras.callbacks import EarlyStopping

    required = max(min_rows, window + 2)
    results = {}
    src = df.groupby(group) if group else [(None, df)]

    for sku, g in src:
        g = g.sort_values(dt).reset_index(drop=True)
        series = g[target].astype(float).values

        if len(series) < required:
            log.debug(f"LSTM: skipping SKU={sku} — {len(series)} rows < {required} required")
            continue

        scaler = MinMaxScaler(feature_range=(0, 1))
        scaled = scaler.fit_transform(series.reshape(-1, 1)).flatten().astype(np.float32)

        X, y_seq = _create_sequences(scaled, window)
        X = X.reshape((X.shape[0], window, 1))

        cut = int(len(X) * train_ratio)
        if cut < 2 or cut >= len(X):
            continue

        X_tr, X_te = X[:cut], X[cut:]
        y_tr, y_te = y_seq[:cut], y_seq[cut:]

        key = str(sku) if sku is not None else "__all__"
        try:
            model = _build_model(window)
            cb = EarlyStopping(
                monitor="val_loss", patience=patience,
                restore_best_weights=True, verbose=0,
            )
            model.fit(
                X_tr, y_tr,
                epochs=epochs, batch_size=32, verbose=0,
                validation_split=0.1,
                callbacks=[cb],
            )

            # Evaluation on test set
            preds_scaled = model.predict(X_te, verbose=0).flatten()
            preds_real = scaler.inverse_transform(
                preds_scaled.reshape(-1, 1)
            ).flatten()
            y_te_real = scaler.inverse_transform(
                y_te.reshape(-1, 1)
            ).flatten()

            mae_val = float(np.mean(np.abs(y_te_real - preds_real)))
            result = {"mae": mae_val}

            if horizon > 0:
                last_window = scaled[-window:]
                p10, p50, p90 = _lstm_mc_forecast(model, scaler, last_window, horizon)
                result["forecast"] = p50
                result["p10"] = p10
                result["p50"] = p50
                result["p90"] = p90

                train_preds_scaled = model.predict(X_tr, verbose=0).flatten()
                train_preds_real = scaler.inverse_transform(
                    train_preds_scaled.reshape(-1, 1)
                ).flatten()
                train_actual_real = scaler.inverse_transform(
                    y_tr.reshape(-1, 1)
                ).flatten()
                result["residuals"] = train_actual_real - train_preds_real

            results[key] = result

        except Exception as e:
            log.warning(f"LSTM failed SKU={sku}: {e}")

    return results

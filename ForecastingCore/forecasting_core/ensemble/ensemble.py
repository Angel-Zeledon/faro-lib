"""Weighted ensemble per SKU based on inverse MAE from validation."""
import numpy as np
from typing import Dict


class WeightedEnsemble:
    def __init__(self): self._weights: Dict[str, Dict[str, float]] = {}

    def fit(self, sku_model_mae: Dict[str, Dict[str, float]]):
        self._weights = {}
        for sku, maes in sku_model_mae.items():
            inv = {m: 1/(v+1e-8) for m, v in maes.items()}
            total = sum(inv.values())
            self._weights[sku] = {m: w/total for m, w in inv.items()}

    def predict(self, sku: str, model_predictions: Dict[str, np.ndarray]) -> np.ndarray:
        weights = self._weights.get(sku)
        preds = list(model_predictions.values())
        if not preds: raise ValueError(f"No predictions for SKU {sku}")
        if weights is None: return np.mean(preds, axis=0)
        result = np.zeros_like(np.array(preds[0], float))
        for model, p in model_predictions.items():
            result += weights.get(model, 0.0) * np.array(p, float)
        return result

    def weights_df(self):
        import pandas as pd
        rows = [{"sku": sku, "model": m, "weight": round(w, 4)}
                for sku, ws in self._weights.items() for m, w in ws.items()]
        return pd.DataFrame(rows).sort_values(["sku", "weight"], ascending=[True, False])



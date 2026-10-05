"""
Per-SKU weighted ensemble, weighted by the inverse of each model's error.

Two things about it are worth knowing before reading a number it produces.

**The score it is given is the one that crowns the champion** — `cost_horizon`
first, walking `CHAMPION_METRIC_ORDER` (see evaluation/metrics.py). It used to
be the 1-step fold MAE of the ML models only, which meant the line drawn on the
chart was built from a metric no other layer used, and every statistical model
in the run was silently given a weight of zero while still appearing in the
average's input.

**It is not a champion candidate.** `Pipeline._flatten` emits no metrics row for
it, so neither `_select_champions` nor `backend/inventory/service.py::
best_model_by_sku` can pick it, and no purchase order is ever computed from it.
Making it eligible would mean measuring it h steps ahead on held-out data the
way every other family is measured — a real evaluation, not a weighting — and
that has not been built.
"""
import numpy as np
from typing import Dict


class WeightedEnsemble:
    def __init__(self): self._weights: Dict[str, Dict[str, float]] = {}

    def fit(self, sku_model_score: Dict[str, Dict[str, float]]):
        """{sku: {model: error}} → normalized inverse-error weights per SKU.

        Lower error means more weight. Any error is accepted as long as the
        same one is used for every model in a SKU — mixing a 1-step score with
        an h-step one inside a series would weight the easier question higher.
        """
        self._weights = {}
        for sku, scores in sku_model_score.items():
            usable = {m: float(v) for m, v in scores.items()
                      if v is not None and np.isfinite(float(v))}
            if not usable:
                continue
            inv = {m: 1 / (v + 1e-8) for m, v in usable.items()}
            total = sum(inv.values())
            self._weights[sku] = {m: w / total for m, w in inv.items()}

    def predict(self, sku: str, model_predictions: Dict[str, np.ndarray]) -> np.ndarray:
        weights = self._weights.get(sku)
        preds = list(model_predictions.values())
        if not preds: raise ValueError(f"No predictions for SKU {sku}")
        if weights is None: return np.mean(preds, axis=0)

        # Renormalize over the models that ACTUALLY produced a forecast. The
        # weights were fitted over every model that has a metrics row, and a
        # model can have one without producing forecast points. Multiplying by
        # a weight that sums to less than 1 pulled the ensemble toward zero —
        # silently, and hardest exactly when a model had failed.
        usable = {m: weights[m] for m in model_predictions if m in weights}
        total = sum(usable.values())
        if total <= 0: return np.mean(preds, axis=0)

        result = np.zeros_like(np.array(preds[0], float))
        for model, weight in usable.items():
            result += (weight / total) * np.array(model_predictions[model], float)
        return result

    def weights_df(self):
        import pandas as pd
        rows = [{"sku": sku, "model": m, "weight": round(w, 4)}
                for sku, ws in self._weights.items() for m, w in ws.items()]
        return pd.DataFrame(rows).sort_values(["sku", "weight"], ascending=[True, False])

"""
Baseline forecasters for fair model comparison.

Every model evaluation MUST compare against these baselines.
A model that doesn't beat naive is not useful in production.

Baselines:
    naive          — last observed value repeated
    seasonal_naive — last full season repeated
    historical_avg — mean of training series
"""

import numpy as np
from forecasting_core.evaluation.metrics import evaluate_all
from typing import Dict


class BaselineEvaluator:

    @staticmethod
    def naive(train: np.ndarray, n_steps: int) -> np.ndarray:
        """Last-value naive: repeats last training observation."""
        return np.full(n_steps, float(train[-1]))

    @staticmethod
    def seasonal_naive(train: np.ndarray, n_steps: int, period: int = 7) -> np.ndarray:
        """Repeats the last full seasonal cycle."""
        preds = []
        for h in range(n_steps):
            idx = len(train) - period + (h % period)
            preds.append(float(train[idx]) if 0 <= idx < len(train) else float(train[-1]))
        return np.array(preds)

    @staticmethod
    def historical_avg(train: np.ndarray, n_steps: int) -> np.ndarray:
        """Mean of training series repeated."""
        return np.full(n_steps, float(np.mean(train)))

    @staticmethod
    def evaluate_baselines(
        train: np.ndarray, test: np.ndarray, period: int = 7
    ) -> Dict[str, Dict[str, float]]:
        """
        Evaluate all baselines against a test set.

        Returns:
            {"naive": {mae, rmse, ...}, "seasonal_naive": {...}, "historical_avg": {...}}
        """
        train, test = np.array(train, float), np.array(test, float)
        n = len(test)
        return {
            "naive":          evaluate_all(test, BaselineEvaluator.naive(train, n)),
            "seasonal_naive": evaluate_all(test, BaselineEvaluator.seasonal_naive(train, n, period)),
            "historical_avg": evaluate_all(test, BaselineEvaluator.historical_avg(train, n)),
        }

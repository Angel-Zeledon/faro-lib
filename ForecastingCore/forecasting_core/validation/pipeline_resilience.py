"""
Pipeline resilience: partial failure handling and model fallback chains.

PartialResultCollector — gathers per-SKU results even when some fail.
FallbackChain         — tries models in order, returns first success.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from forecasting_core.validation.exceptions import SKUTrainingError

log = logging.getLogger(__name__)

# Default fallback order: most capable → most robust
DEFAULT_FALLBACK_CHAIN = [
    "lightgbm",
    "xgboost",
    "linear",
    "ets",
    "seasonal_naive",
    "naive",
]


# ---------------------------------------------------------------------------
# Partial result collector
# ---------------------------------------------------------------------------

@dataclass
class SkuResult:
    sku: str
    model: str
    metrics: Dict[str, float]
    success: bool = True
    error: Optional[str] = None


class PartialResultCollector:
    """
    Collect training results across SKUs, tolerating per-SKU failures.

    Usage:
        collector = PartialResultCollector()
        for sku in skus:
            try:
                result = train_sku(sku)
                collector.record_success(sku, model_name, result)
            except Exception as e:
                collector.record_failure(sku, model_name, e)
        summary = collector.summary()
    """

    def __init__(self, fail_fast_threshold: float = 1.0):
        """
        Args:
            fail_fast_threshold: If failure rate exceeds this fraction, raise PipelineError.
                                 1.0 = never fail-fast (collect all).
        """
        self._results: List[SkuResult] = []
        self._threshold = fail_fast_threshold

    def record_success(self, sku: str, model: str, metrics: Dict[str, float]):
        self._results.append(SkuResult(sku=sku, model=model, metrics=metrics))

    def record_failure(self, sku: str, model: str, error: Exception):
        err = SKUTrainingError(sku=sku, model=model, cause=error)
        log.warning(str(err))
        self._results.append(SkuResult(sku=sku, model=model, metrics={}, success=False, error=str(error)))
        self._check_threshold()

    def _check_threshold(self):
        if not self._results:
            return
        fail_rate = sum(1 for r in self._results if not r.success) / len(self._results)
        if fail_rate > self._threshold:
            from forecasting_core.validation.exceptions import PipelineError
            raise PipelineError(
                f"Failure rate {fail_rate:.0%} exceeds threshold {self._threshold:.0%}. "
                "Aborting pipeline.",
                error_id="FAIL_RATE_EXCEEDED",
                context={"fail_rate": round(fail_rate, 3), "threshold": self._threshold},
            )

    @property
    def successes(self) -> List[SkuResult]:
        return [r for r in self._results if r.success]

    @property
    def failures(self) -> List[SkuResult]:
        return [r for r in self._results if not r.success]

    def success_rate(self) -> float:
        if not self._results:
            return 0.0
        return sum(1 for r in self._results if r.success) / len(self._results)

    def summary(self) -> dict:
        return {
            "total": len(self._results),
            "succeeded": len(self.successes),
            "failed": len(self.failures),
            "success_rate": round(self.success_rate(), 3),
            "failed_skus": [r.sku for r in self.failures],
        }

    def to_results_dict(self) -> Dict[str, dict]:
        """Flatten to {model_sku: metrics_dict} for compatibility with Trainer output."""
        return {
            f"{r.model}_{r.sku}": {**r.metrics, "sku": r.sku, "model": r.model}
            for r in self.successes
        }


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------

class FallbackChain:
    """
    Tries each model in a priority list; returns the first successful result.

    Usage:
        chain = FallbackChain(["lightgbm", "xgboost", "ets", "naive"])
        result = chain.run(sku, series, train_fn_map)
    """

    def __init__(self, chain: List[str] = None):
        self.chain = chain or DEFAULT_FALLBACK_CHAIN

    def run(
        self,
        sku: str,
        train_fns: Dict[str, Callable[[], Dict[str, float]]],
    ) -> Tuple[str, Dict[str, float]]:
        """
        Args:
            sku:       SKU identifier (for logging).
            train_fns: {model_name: callable() → metrics dict}

        Returns:
            (model_name_used, metrics_dict)

        Raises:
            RuntimeError if all models in chain fail.
        """
        errors = []
        for model in self.chain:
            if model not in train_fns:
                continue
            try:
                metrics = train_fns[model]()
                if metrics:
                    log.debug(f"SKU={sku}: fallback chain succeeded with '{model}'")
                    return model, metrics
            except Exception as e:
                log.debug(f"SKU={sku}: model '{model}' failed ({e}), trying next")
                errors.append(f"{model}: {e}")

        raise RuntimeError(
            f"All models in fallback chain failed for SKU={sku}. "
            f"Errors: {'; '.join(errors)}"
        )


def naive_forecast(series: np.ndarray, n_steps: int) -> np.ndarray:
    """Trivial last-value naive forecast — ultimate fallback."""
    return np.full(n_steps, float(series[-1]) if len(series) else 0.0)


def seasonal_naive_forecast(series: np.ndarray, n_steps: int, period: int = 7) -> np.ndarray:
    """Seasonal naive — repeat last full season."""
    preds = []
    for h in range(n_steps):
        idx = len(series) - period + (h % period)
        preds.append(float(series[idx]) if 0 <= idx < len(series) else float(series[-1]))
    return np.array(preds)

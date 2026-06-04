"""Drift detection: PSI + KS test for data, feature and prediction drift."""
import numpy as np
from typing import Dict, List

PSI_THRESHOLDS = {"none": 0.10, "moderate": 0.20, "significant": 0.25}

def psi(expected, actual, n_bins=10) -> float:
    expected, actual = np.array(expected, float), np.array(actual, float)
    edges = np.percentile(np.concatenate([expected, actual]), np.linspace(0, 100, n_bins + 1))
    edges[0] -= 1e-8; edges[-1] += 1e-8
    e = np.histogram(expected, edges)[0] / (len(expected) + 1e-8)
    a = np.histogram(actual,   edges)[0] / (len(actual)   + 1e-8)
    e, a = np.where(e == 0, 1e-8, e), np.where(a == 0, 1e-8, a)
    return float(np.sum((a - e) * np.log(a / e)))

def psi_level(v) -> str:
    if v >= PSI_THRESHOLDS["significant"]: return "significant"
    if v >= PSI_THRESHOLDS["moderate"]:   return "moderate"
    if v >= PSI_THRESHOLDS["none"]:       return "low"
    return "none"

def ks_test(ref, cur) -> dict:
    from scipy import stats
    stat, p = stats.ks_2samp(ref, cur)
    return {"statistic": float(stat), "p_value": float(p), "drift": bool(p < 0.05)}


class DriftDetector:
    """Compares reference vs current datasets for statistical drift."""

    def __init__(self, reference_df, target_col: str, group_col: str):
        self.reference = reference_df
        self.target_col = target_col
        self.group_col  = group_col

    def detect_feature_drift(self, current_df) -> Dict[str, dict]:
        report = {}
        for col in self.reference.select_dtypes(include=np.number).columns:
            if col in (self.target_col, self.group_col): continue
            ref = self.reference[col].dropna().values
            cur = current_df[col].dropna().values if col in current_df else np.array([])
            if len(ref) < 10 or len(cur) < 10: continue
            p = psi(ref, cur)
            ks = ks_test(ref, cur)
            report[col] = {"psi": round(p, 4), "psi_level": psi_level(p),
                           "ks_p_value": ks["p_value"],
                           "drift": ks["drift"] or p >= PSI_THRESHOLDS["moderate"]}
        return report

    def detect_prediction_drift(self, ref_preds, cur_preds) -> dict:
        p = psi(ref_preds, cur_preds)
        ks = ks_test(ref_preds, cur_preds)
        return {"psi": round(p, 4), "psi_level": psi_level(p),
                "ks_p_value": ks["p_value"],
                "drift_detected": ks["drift"] or p >= PSI_THRESHOLDS["moderate"]}

    def alerts(self, report: Dict) -> List[str]:
        return [f"DRIFT [{v['psi_level'].upper()}] {col}: PSI={v['psi']}"
                for col, v in report.items() if v.get("drift")]

    @staticmethod
    def performance_decay(
        historical_maes: List[float],
        current_mae: float,
        threshold_pct: float = 0.20,
    ) -> dict:
        """Detect if current MAE has degraded more than threshold_pct vs historical baseline."""
        if not historical_maes:
            return {"decay": False, "message": "No historical MAE data"}
        baseline = float(np.mean(historical_maes))
        degradation = (current_mae - baseline) / (baseline + 1e-8)
        return {
            "baseline_mae": round(baseline, 4),
            "current_mae": round(current_mae, 4),
            "degradation_pct": round(degradation * 100, 2),
            "decay": degradation > threshold_pct,
            "retrain_recommended": degradation > threshold_pct,
        }

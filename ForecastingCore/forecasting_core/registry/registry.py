"""Model registry — tracks experiment runs with metrics and config hashes."""
import json, os
from datetime import datetime
from typing import Any, Dict, List, Optional


class ModelRegistry:
    def __init__(self, path: str = "registry.json"):
        self.path = path
        self._data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                try: return json.load(f)
                except: pass
        return {"runs": []}

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2, default=str)

    def log_run(self, session_name: str, config_hash: str,
                results: Dict[str, Any], metadata: Optional[Dict] = None) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"{session_name}_{config_hash}_{ts}"
        self._data["runs"].append({
            "run_id": run_id, "session": session_name, "config_hash": config_hash,
            "timestamp": datetime.now().isoformat(), "results": results, "metadata": metadata or {},
        })
        self._save()
        return run_id

    def list_runs(self, session: Optional[str] = None) -> List[Dict]:
        runs = self._data["runs"]
        if session: runs = [r for r in runs if r["session"] == session]
        return sorted(runs, key=lambda r: r["timestamp"], reverse=True)

    def best_run(self, session: str, metric: str = "mae") -> Optional[Dict]:
        import numpy as np
        runs = self.list_runs(session)
        if not runs: return None
        def avg(run):
            vals = [v[metric] for v in run["results"].values()
                    if isinstance(v, dict) and metric in v]
            return float(np.mean(vals)) if vals else float("inf")
        return min(runs, key=avg)

    def compare_sessions(self, metric: str = "mae") -> dict:
        """Best metric value per session across all runs."""
        import numpy as np
        by_session: Dict[str, List[float]] = {}
        for run in self._data["runs"]:
            vals = [v[metric] for v in run["results"].values()
                    if isinstance(v, dict) and metric in v]
            if vals:
                by_session.setdefault(run["session"], []).append(float(np.mean(vals)))
        return {s: min(vals) for s, vals in by_session.items()}

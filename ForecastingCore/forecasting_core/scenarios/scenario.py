"""
ScenarioEngine — apply what-if adjustments to a forecast DataFrame.

Supports per-SKU, per-model, and per-date-range rules with:
    multiplier  — scale the forecast (e.g. 1.10 for +10% demand)
    offset      — add a fixed amount after multiplication
    floor       — minimum value after adjustment
    ceiling     — maximum value after adjustment

Confidence bands (p90_lo / p90_hi) are adjusted proportionally.

Example:
    from forecasting_core.scenarios import ScenarioEngine, ScenarioRule

    rules = [
        ScenarioRule(multiplier=1.10),                          # +10% all SKUs
        ScenarioRule(sku="SKU_A", multiplier=1.25),             # +25% for SKU_A only
        ScenarioRule(sku="SKU_B", date_start="2025-06-01",
                     date_end="2025-06-30", multiplier=0.80),   # promo drop
        ScenarioRule(floor=0.0),                                # never negative
    ]

    adjusted_df  = ScenarioEngine.apply(forecast_df, rules)
    adjusted_dict = ScenarioEngine.apply_to_dict(forecast_dict, rules)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd


@dataclass
class ScenarioRule:
    """
    One adjustment rule in a what-if scenario.

    All filters (sku, model, date_start, date_end) are ANDed.
    If a filter is None it matches everything in that dimension.

    Attributes
    ----------
    sku         : str | None  — target SKU; None = all SKUs
    model       : str | None  — target model; None = all models
    multiplier  : float       — multiply forecast values (default 1.0 = no change)
    offset      : float       — add after multiplication (default 0.0)
    date_start  : str | None  — ISO date "YYYY-MM-DD", inclusive
    date_end    : str | None  — ISO date "YYYY-MM-DD", inclusive
    floor       : float | None — clip result to minimum this value
    ceiling     : float | None — clip result to maximum this value
    label       : str         — human-readable description shown in reports
    """

    sku:        Optional[str]   = None
    model:      Optional[str]   = None
    multiplier: float           = 1.0
    offset:     float           = 0.0
    date_start: Optional[str]   = None
    date_end:   Optional[str]   = None
    floor:      Optional[float] = None
    ceiling:    Optional[float] = None
    label:      str             = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None or k in ("multiplier", "offset")}

    @classmethod
    def from_dict(cls, d: dict) -> "ScenarioRule":
        fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in fields})


class ScenarioEngine:
    """
    Applies a list of ScenarioRules to a forecast DataFrame.

    Rules are applied in order — later rules can compound earlier ones.
    """

    @staticmethod
    def apply(
        forecast_df: pd.DataFrame,
        rules: List[ScenarioRule],
    ) -> pd.DataFrame:
        """
        Apply scenario rules to a forecast DataFrame.

        Parameters
        ----------
        forecast_df : DataFrame with columns [sku, model, date, forecast, p90_lo, p90_hi]
        rules       : list of ScenarioRule (or dicts with the same keys)

        Returns
        -------
        New DataFrame with adjusted forecast values.
        """
        if forecast_df is None or forecast_df.empty:
            return forecast_df if forecast_df is not None else pd.DataFrame()

        df = forecast_df.copy()
        has_date = "date" in df.columns
        if has_date:
            df["_date_ts"] = pd.to_datetime(df["date"], errors="coerce")

        parsed = [ScenarioRule.from_dict(r) if isinstance(r, dict) else r for r in rules]

        for rule in parsed:
            mask = pd.Series(True, index=df.index)

            if rule.sku is not None:
                mask &= df["sku"].astype(str) == str(rule.sku)
            if rule.model is not None and "model" in df.columns:
                mask &= df["model"].astype(str) == str(rule.model)
            if rule.date_start and has_date:
                mask &= df["_date_ts"] >= pd.to_datetime(rule.date_start)
            if rule.date_end and has_date:
                mask &= df["_date_ts"] <= pd.to_datetime(rule.date_end)

            if not mask.any():
                continue

            m, o = rule.multiplier, rule.offset

            df.loc[mask, "forecast"] = df.loc[mask, "forecast"] * m + o

            if "p90_lo" in df.columns:
                df.loc[mask, "p90_lo"] = df.loc[mask, "p90_lo"] * m + o
            if "p90_hi" in df.columns:
                df.loc[mask, "p90_hi"] = df.loc[mask, "p90_hi"] * m + o

            if rule.floor is not None:
                df.loc[mask, "forecast"] = df.loc[mask, "forecast"].clip(lower=rule.floor)
                if "p90_lo" in df.columns:
                    df.loc[mask, "p90_lo"] = df.loc[mask, "p90_lo"].clip(lower=rule.floor)
                if "p90_hi" in df.columns:
                    df.loc[mask, "p90_hi"] = df.loc[mask, "p90_hi"].clip(lower=rule.floor)

            if rule.ceiling is not None:
                df.loc[mask, "forecast"] = df.loc[mask, "forecast"].clip(upper=rule.ceiling)
                if "p90_hi" in df.columns:
                    df.loc[mask, "p90_hi"] = df.loc[mask, "p90_hi"].clip(upper=rule.ceiling)

        if has_date and "_date_ts" in df.columns:
            df = df.drop(columns=["_date_ts"])

        return df

    @staticmethod
    def apply_to_dict(
        forecast_dict: dict,
        rules: List[ScenarioRule],
    ) -> dict:
        """
        Apply scenario rules to the {sku: {model: [{date, value, lower, upper}]}} format.

        Converts to DataFrame internally, applies rules, converts back.
        """
        if not forecast_dict:
            return {}

        rows = []
        for sku, model_dict in forecast_dict.items():
            for model_name, pts in model_dict.items():
                for i, pt in enumerate(pts):
                    rows.append({
                        "sku": sku, "model": model_name,
                        "date": pt["date"],
                        "forecast": pt["value"],
                        "p90_lo": pt.get("lower", 0.0),
                        "p90_hi": pt.get("upper", 0.0),
                        "step": i + 1,
                    })

        if not rows:
            return {}

        df = pd.DataFrame(rows)
        df = ScenarioEngine.apply(df, rules)

        result: dict = {}
        for (sku, model), grp in df.groupby(["sku", "model"]):
            pts = []
            for _, row in grp.sort_values("step").iterrows():
                pts.append({
                    "date":  str(row["date"])[:10],
                    "value": round(float(row["forecast"]), 4),
                    "lower": round(float(row["p90_lo"]), 4),
                    "upper": round(float(row["p90_hi"]), 4),
                })
            result.setdefault(str(sku), {})[str(model)] = pts

        return result

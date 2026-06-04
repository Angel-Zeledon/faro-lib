"""
InventoryAdvisor — Decision Intelligence layer.

Converts forecasts into actionable inventory recommendations:
  - Reorder Point (ROP)
  - Safety Stock
  - Stockout Risk
  - Days of Coverage
  - Overstock Alert

These recommendations are fully driven by the forecast output and
business parameters configured in BusinessConfig.

Example:
    from forecasting_core.business.inventory import InventoryAdvisor

    advisor = InventoryAdvisor(service_level=0.95, lead_time_days=7)
    rec = advisor.recommend(
        sku="SKU_001",
        forecast=np.array([120, 130, 115, ...]),   # daily forecast for horizon
        current_stock=500,
        demand_std=25.0,
    )
    # rec.reorder_point, rec.safety_stock, rec.stockout_risk, rec.action
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from typing import List, Optional
from scipy import stats


@dataclass
class InventoryRecommendation:
    """
    Inventory recommendation for a single SKU.

    Attributes:
        sku:              SKU identifier.
        forecast_mean:    Mean forecast over the horizon.
        forecast_total:   Total demand forecast over horizon.
        safety_stock:     Units of safety stock to hold.
        reorder_point:    Stock level at which to reorder.
        days_of_coverage: Current stock / daily demand rate.
        stockout_risk:    Probability of stockout (0-1).
        overstock_alert:  True if current stock exceeds ROP + safety stock by 50%.
        action:           Human-readable recommendation ("REORDER", "OK", "OVERSTOCK").
        details:          Dict with intermediate calculation values.
    """
    sku: str
    forecast_mean: float
    forecast_total: float
    safety_stock: float
    reorder_point: float
    days_of_coverage: float
    stockout_risk: float
    overstock_alert: bool
    action: str
    details: dict


class InventoryAdvisor:
    """
    Computes inventory recommendations from forecast outputs.

    Args:
        service_level:            Target service level (e.g. 0.95 = 95%).
        lead_time_days:           Supplier lead time in days.
        holding_cost_pct:         Annual holding cost as % of unit value.
        stockout_cost_multiplier: Multiplier for stockout vs holding cost.
    """

    def __init__(
        self,
        service_level: float = 0.95,
        lead_time_days: int = 7,
        holding_cost_pct: float = 0.20,
        stockout_cost_multiplier: float = 3.0,
    ):
        if not (0.0 < service_level < 1.0):
            raise ValueError(
                f"service_level must be in the open interval (0, 1), got {service_level}. "
                "Typical values: 0.90, 0.95, 0.99."
            )
        self.service_level = service_level
        self.lead_time_days = lead_time_days
        self.holding_cost_pct = holding_cost_pct
        self.stockout_cost_multiplier = stockout_cost_multiplier
        self._z = float(stats.norm.ppf(service_level))  # z-score for service level

    def recommend(
        self,
        sku: str,
        forecast: np.ndarray,
        current_stock: float = 0.0,
        demand_std: Optional[float] = None,
    ) -> InventoryRecommendation:
        """
        Generate an inventory recommendation for one SKU.

        Args:
            sku:           SKU identifier.
            forecast:      Daily demand forecast array (length = horizon).
            current_stock: Current on-hand inventory units.
            demand_std:    Daily demand standard deviation (estimated from forecast if None).

        Returns:
            InventoryRecommendation dataclass.
        """
        forecast = np.array(forecast, float)
        if len(forecast) == 0:
            raise ValueError(
                f"forecast array for SKU '{sku}' is empty. "
                "Provide at least one forecast value."
            )
        daily_demand = float(np.mean(forecast))
        total_demand = float(np.sum(forecast))

        if demand_std is None:
            demand_std = float(np.std(forecast)) if len(forecast) > 1 else daily_demand * 0.3

        safety_stock = self._z * demand_std * np.sqrt(self.lead_time_days)
        reorder_point = daily_demand * self.lead_time_days + safety_stock
        days_coverage = current_stock / (daily_demand + 1e-8)

        # Stockout risk: probability demand during lead time > current stock
        lead_demand_mean = daily_demand * self.lead_time_days
        lead_demand_std  = demand_std * np.sqrt(self.lead_time_days)
        stockout_risk = float(1 - stats.norm.cdf(current_stock, lead_demand_mean, lead_demand_std + 1e-8))
        stockout_risk = min(max(stockout_risk, 0.0), 1.0)

        overstock_alert = current_stock > (reorder_point + safety_stock) * 1.5

        if current_stock <= reorder_point:
            action = "REORDER"
        elif overstock_alert:
            action = "OVERSTOCK"
        else:
            action = "OK"

        return InventoryRecommendation(
            sku=sku,
            forecast_mean=round(daily_demand, 2),
            forecast_total=round(total_demand, 2),
            safety_stock=round(safety_stock, 2),
            reorder_point=round(reorder_point, 2),
            days_of_coverage=round(days_coverage, 1),
            stockout_risk=round(stockout_risk, 4),
            overstock_alert=overstock_alert,
            action=action,
            details={
                "z_score":           round(self._z, 3),
                "lead_time_days":    self.lead_time_days,
                "service_level":     self.service_level,
                "daily_demand":      round(daily_demand, 2),
                "demand_std":        round(demand_std, 2),
                "current_stock":     current_stock,
            },
        )

    def batch_recommend(
        self,
        forecasts_by_sku: dict,
        stocks_by_sku: Optional[dict] = None,
        std_by_sku: Optional[dict] = None,
    ) -> List[InventoryRecommendation]:
        """
        Recommend for all SKUs at once.

        Args:
            forecasts_by_sku: {sku: np.ndarray of forecast}
            stocks_by_sku:    {sku: current_stock}   — defaults to 0
            std_by_sku:       {sku: demand_std}      — estimated if None

        Returns:
            List of InventoryRecommendation sorted by stockout_risk descending.
        """
        stocks = stocks_by_sku or {}
        stds   = std_by_sku or {}
        results = []
        for sku, fc in forecasts_by_sku.items():
            rec = self.recommend(
                sku=sku,
                forecast=fc,
                current_stock=stocks.get(sku, 0.0),
                demand_std=stds.get(sku),
            )
            results.append(rec)
        return sorted(results, key=lambda r: r.stockout_risk, reverse=True)

    def summary_df(self, recommendations: List[InventoryRecommendation]):
        """Convert recommendations to a DataFrame for reporting."""
        import pandas as pd
        return pd.DataFrame([{
            "sku":              r.sku,
            "action":           r.action,
            "stockout_risk":    r.stockout_risk,
            "reorder_point":    r.reorder_point,
            "safety_stock":     r.safety_stock,
            "days_coverage":    r.days_of_coverage,
            "forecast_mean":    r.forecast_mean,
            "overstock_alert":  r.overstock_alert,
        } for r in recommendations])

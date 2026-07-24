"""
Shared formatting helpers for user-facing text the backend generates
(narratives, recommendations, PDF exports).

Money is the anchor market's currency — Costa Rican colón (₡). Every user-facing
amount used to hardcode `$`, which was wrong for the market and scattered across
narrative_service, service and po_pdf. Keep it here so there is one place to
change if the currency ever needs to vary per tenant.
"""

CURRENCY_SYMBOL = "₡"


def money(amount: float, decimals: int = 0) -> str:
    """
    Format an amount as colones: ₡1,234 (or ₡1,234.56 with decimals=2).
    Thousands separated with a comma, matching the rest of the app's copy.
    """
    return f"{CURRENCY_SYMBOL}{amount:,.{decimals}f}"


def format_days(n: float) -> str:
    """
    Day count that agrees in number: "1 día" / "N días". Every user-facing
    sentence that interpolates a day count must go through this — otherwise the
    product reads "1 días de stock" on exactly the screen shown when a product
    is about to run out.
    """
    rounded = round(n)
    return "1 día" if rounded == 1 else f"{rounded:,.0f} días"


# Coverage in a period-trained session (multi-period Phase C) comes out in the
# ACTIVE period's unit — a weekly session's "3" means 3 weeks, not 3 days. Text
# that interpolates a coverage figure must carry the matching noun, or `/hoy`
# reads "32 días de cobertura" for what is really 32 weeks. Keyed on the planning
# period (daily/weekly/monthly); "daily" is byte-identical to ``format_days``.
_COVERAGE_WORDS = {
    "daily":   ("día", "días"),
    "weekly":  ("semana", "semanas"),
    "monthly": ("mes", "meses"),
}


def format_coverage(n: float, period: str = "daily") -> str:
    """Coverage figure that agrees in number AND unit for the active planning
    period: "1 semana" / "N semanas" under weekly, "1 día" / "N días" under
    daily (or any unknown/legacy period)."""
    rounded = round(n)
    singular, plural = _COVERAGE_WORDS.get(period or "daily", _COVERAGE_WORDS["daily"])
    return f"1 {singular}" if rounded == 1 else f"{rounded:,.0f} {plural}"

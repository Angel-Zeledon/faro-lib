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

"""
Shared formatting helpers for user-facing text the backend generates
(narratives, recommendations, PDF exports).

Money follows the tenant's currency setting (`backend/api/v1/currency.py`), not a
hardcoded symbol. The frontend already honoured that setting for every figure it
renders itself; the strings the BACKEND composes — the executive summary, the
purchase-order PDF, the monthly recap email — did not, so a customer who chose
USD still read ₡ on exactly the documents they forward to other people.

Two rules that come with it:

  · The setting RELABELS, it never converts. Faro stores the amount it was given
    and never guesses an exchange rate.
  · Resolving a tenant's currency is a DB read. Resolve it ONCE per document or
    narrative and pass the dict down; never call the reader inside a row loop.

`money()` with no currency renders the anchor market's colón, so a caller that
has not been migrated degrades to exactly what the product did before the
setting existed rather than to a bare number with no symbol.
"""

# Anchor market. Mirrors DEFAULT_CODE / SUPPORTED["CRC"] in backend/api/v1/currency.py.
DEFAULT_CURRENCY = {"code": "CRC", "symbol": "₡", "locale": "es-CR", "decimals": 0}

CURRENCY_SYMBOL = DEFAULT_CURRENCY["symbol"]


def money(amount: float, decimals: int | None = None, currency: dict | None = None) -> str:
    """
    Format an amount in the tenant's currency: ₡1,234 (CRC) / $1,234.56 (USD).
    Thousands separated with a comma, matching the rest of the app's copy.

    `currency` is what `currency_of(tenant_id)` returns; omitting it falls back to
    the anchor market (CRC).

    `decimals` defaults to the currency's OWN declared precision, so a 0-decimal
    currency (CRC, COP, CLP, ARS) never renders phantom cents and a 2-decimal one
    never silently drops them. Pass it explicitly only to override that.
    """
    cur = currency or DEFAULT_CURRENCY
    symbol = cur.get("symbol") or DEFAULT_CURRENCY["symbol"]
    if decimals is None:
        declared = cur.get("decimals")
        decimals = declared if isinstance(declared, int) else 0
    return f"{symbol}{amount:,.{decimals}f}"


# The Spanish nouns live in `backend/notifications/locale.py`, keyed in English,
# because that is where copy for the channels the frontend never renders belongs
# (CLAUDE.md, Language). These helpers only decide WHICH key applies — the number
# agreement and the unit — never the wording.
#
# Which means: what these produce is Spanish, so it may only reach a PDF, an
# email or a WhatsApp message. An API response that the frontend renders must
# carry the raw number instead and let the catalogue supply the noun; use
# ``format_coverage_en`` for the English fallback text such a response ships.
def format_days(n: float) -> str:
    """
    Day count that agrees in number: "1 día" / "N días". Every user-facing
    sentence that interpolates a day count must go through this — otherwise the
    product reads "1 días de stock" on exactly the screen shown when a product
    is about to run out.
    """
    from backend.notifications.locale import render_es
    rounded = round(n)
    return render_es("unit_day_one") if rounded == 1 else render_es("unit_day_many", n=f"{rounded:,.0f}")


# Coverage in a period-trained session (multi-period Phase C) comes out in the
# ACTIVE period's unit — a weekly session's "3" means 3 weeks, not 3 days. Text
# that interpolates a coverage figure must carry the matching noun, or `/hoy`
# reads "32 días de cobertura" for what is really 32 weeks. Keyed on the planning
# period (daily/weekly/monthly); "daily" is byte-identical to ``format_days``.
_COVERAGE_KEYS = {
    "daily":   ("unit_day_one", "unit_day_many"),
    "weekly":  ("unit_week_one", "unit_week_many"),
    "monthly": ("unit_month_one", "unit_month_many"),
}

_COVERAGE_WORDS_EN = {
    "daily":     ("day", "days"),
    "weekly":    ("week", "weeks"),
    "monthly":   ("month", "months"),
    "quarterly": ("quarter", "quarters"),
    "yearly":    ("year", "years"),
}


def format_coverage(n: float, period: str = "daily") -> str:
    """Coverage figure that agrees in number AND unit for the active planning
    period: "1 semana" / "N semanas" under weekly, "1 día" / "N días" under
    daily (or any unknown/legacy period). Spanish — see the note above."""
    from backend.notifications.locale import render_es
    rounded = round(n)
    one_key, many_key = _COVERAGE_KEYS.get(period or "daily", _COVERAGE_KEYS["daily"])
    return render_es(one_key) if rounded == 1 else render_es(many_key, n=f"{rounded:,.0f}")


def format_coverage_en(n: float, period: str = "daily") -> str:
    """The same figure in English, for the fallback sentence an API response
    carries alongside its code + params.

    It exists because the alternative kept happening: `format_coverage` got
    interpolated into an English sentence and the screen read "Azucar has 32
    días of coverage".
    """
    rounded = round(n)
    singular, plural = _COVERAGE_WORDS_EN.get(period or "daily", _COVERAGE_WORDS_EN["daily"])
    return f"1 {singular}" if rounded == 1 else f"{rounded:,.0f} {plural}"

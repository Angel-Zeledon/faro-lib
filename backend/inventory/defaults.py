"""
The single source of truth for the inventory planning values Faro assumes when
the tenant has configured nothing — and for the vocabulary that says WHERE a
value came from.

Why this module exists
----------------------
The same product shipped three different lead-time defaults: 7 in the canonical
column defaults, 7 in the training runner's business config, and 15 in the DB
schema, the Quick Start wizard and three spots in /inventory. A recommendation
built on 7 and an explanation built on 15 cannot both be right, and nobody could
tell which one a given SKU had used.

Why 15 and not 7
----------------
Business call, made deliberately: the target user is a LatAm distributor whose
replenishment usually involves an import leg (customs + inland transport), not a
next-week local resupply. 7 days flatters the numbers — it shrinks the reorder
point and the safety stock, so the product recommends ordering LATER than
reality warrants and the buyer discovers the error as a stockout. When we have
to guess, guessing long is the cheap mistake. The unacceptable part was never
"7 vs 15", it was three coexisting answers.

ForecastingCore note: `forecasting_core/data/canonical.py` repeats the literal
15 instead of importing this module, because the layering forbids ForecastingCore
from depending on `backend`. `backend/tests/test_lead_time_provenance.py` asserts
the two are equal, so the duplication cannot silently drift back apart.
"""

from __future__ import annotations

# What Faro assumes when the tenant has told us nothing. See the module
# docstring for the 15-vs-7 reasoning.
DEFAULT_LEAD_TIME_DAYS = 15
DEFAULT_SERVICE_LEVEL = 0.95
DEFAULT_MOQ = 1.0
# Matches the training runner's business-config fallback (`runner.py`), which is
# the only other place this number is currently assumed.
DEFAULT_HOLDING_COST_PCT = 0.20


# ── Provenance vocabulary ────────────────────────────────────────────────────
# The five real ways a planning value gets its number. This replaces THREE
# partial vocabularies that described the same thing differently:
#   service.resolve_lead_time      → 'learned' | 'configured'
#   reception_service              → 'observed' | 'declared' | 'default'
#   (and the DB, which could not express provenance at all)
#
#   'user'          the buyer typed it on the SKU card
#   'file'          it came in on an uploaded file (sales history or stock import)
#   'supplier_rule' it came from a `stock_defaults` rule (supplier / category /
#                   global scope — see SOURCE_SUPPLIER_RULE's note)
#   'learned'       Faro derived it from the supplier's real recorded receptions
#   'default'       nobody configured anything; this is our assumption
#
# 'default' is the value that makes the whole exercise worth the migration:
# without it, `lead_time_days INT NOT NULL DEFAULT 15` made "the user chose 15"
# and "nobody ever touched this" physically indistinguishable, and the
# explanation told the user their supplier's lead time was "configurado".
SOURCE_USER = "user"
SOURCE_FILE = "file"
# A `stock_defaults` hit at ANY scope reports as 'supplier_rule'. Category and
# global rules are the same mechanism ("a rule, not this SKU"), and the
# vocabulary is deliberately five values wide, not seven. Which scope actually
# won travels alongside as `<field>_rule_scope` ∈ supplier|category|global, so
# the copy can state the precedence explicitly (the lesson from the event
# multipliers: the user must be able to tell WHY a SKU got the number it got).
SOURCE_SUPPLIER_RULE = "supplier_rule"
SOURCE_LEARNED = "learned"
SOURCE_DEFAULT = "default"

VALUE_SOURCES: tuple[str, ...] = (
    SOURCE_USER,
    SOURCE_FILE,
    SOURCE_SUPPLIER_RULE,
    SOURCE_LEARNED,
    SOURCE_DEFAULT,
)

# Sources that mean "this number is Faro's assumption, not the tenant's data".
# The UI badges these as estimated; everything else is the tenant's own input or
# evidence derived from it.
ASSUMED_SOURCES: frozenset[str] = frozenset({SOURCE_DEFAULT})

# The inventory_stock fields that carry a `<field>_set_by` provenance column.
# Adding a field here is not enough — it also needs its migration in
# backend/db/migrations.py and a branch in the cascade resolver.
PROVENANCE_FIELDS: tuple[str, ...] = (
    "lead_time_days",
    "service_level",
    "unit_cost",
    "moq",
)

# System defaults keyed by the inventory_stock column they back. `unit_cost` is
# deliberately absent: there is no honest default price for a product we have
# never been told the cost of, and inventing one would put a fabricated number
# into inventory valuation. It stays None and the UI reports it as missing.
SYSTEM_DEFAULTS: dict[str, float | int | None] = {
    "lead_time_days": DEFAULT_LEAD_TIME_DAYS,
    "service_level": DEFAULT_SERVICE_LEVEL,
    "moq": DEFAULT_MOQ,
    "unit_cost": None,
}


def is_assumed(source: str | None) -> bool:
    """True when the value shown is Faro's assumption rather than tenant data."""
    return (source or SOURCE_DEFAULT) in ASSUMED_SOURCES

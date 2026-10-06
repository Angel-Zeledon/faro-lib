"""Schema for multi-currency (see `backend/fx/reference.py` for the rules).

Additive only. A tenant with no rates and no foreign-currency line is untouched:
every new column is NULL (or 0) for it, and every reader treats NULL `currency`
as "the tenant's own currency", which is what every row meant before.
"""

MIGRATIONS: list[tuple[str, str]] = [
    # A dated exchange rate, entered by the tenant. `rate` says how many units of
    # `base_currency` ONE unit of `currency` is worth ("1 USD = 520.50 CRC" is
    # currency=USD, base_currency=CRC, rate=520.5). The base currency is stored on
    # the row, not looked up: when the tenant relabels its base currency the old
    # rates stop applying (a missing rate, said out loud) instead of silently
    # meaning something else.
    ("create_exchange_rates",
     """CREATE TABLE IF NOT EXISTS exchange_rates (
         id              TEXT PRIMARY KEY,
         tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         currency        TEXT NOT NULL,
         base_currency   TEXT NOT NULL,
         rate            NUMERIC(24, 10) NOT NULL CHECK (rate > 0),
         effective_date  DATE NOT NULL,
         source_note     TEXT,
         created_by      TEXT NOT NULL,
         created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         CHECK (currency <> base_currency),
         UNIQUE (tenant_id, currency, base_currency, effective_date)
     )"""),
    ("create_exchange_rates_lookup_idx",
     "CREATE INDEX IF NOT EXISTS exchange_rates_lookup_idx "
     "ON exchange_rates (tenant_id, base_currency, currency, effective_date DESC)"),

    # What a supplier charges for a SKU may be in a currency other than the
    # tenant's. NULL = the tenant's own currency (every row that exists today).
    ("add_sku_suppliers_currency",
     "ALTER TABLE sku_suppliers ADD COLUMN IF NOT EXISTS currency TEXT"),

    # Purchase-order lines. `currency` is set ONLY when the line is priced in a
    # currency other than the tenant's at the time the order was written; NULL is
    # the tenant's own currency (all existing rows). `unit_cost` stays in the
    # line's own currency. The rate used is recorded ON THE LINE, with the date
    # and id of the rate row it came from, so an order is never re-converted by a
    # later rate: `value_base` (qty x unit_cost x rate, rounded half up to 2
    # decimals, in `fx_base_currency`) is what was true when the order was
    # written. A foreign line with a cost and no rate on that day has
    # `value_base` NULL: unconverted, counted and reported, never valued at 1.0.
    ("add_po_items_currency",
     "ALTER TABLE inventory_po_items ADD COLUMN IF NOT EXISTS currency TEXT"),
    ("add_po_items_fx_base_currency",
     "ALTER TABLE inventory_po_items ADD COLUMN IF NOT EXISTS fx_base_currency TEXT"),
    ("add_po_items_fx_rate",
     "ALTER TABLE inventory_po_items ADD COLUMN IF NOT EXISTS fx_rate NUMERIC(24, 10)"),
    ("add_po_items_fx_rate_date",
     "ALTER TABLE inventory_po_items ADD COLUMN IF NOT EXISTS fx_rate_date DATE"),
    ("add_po_items_fx_rate_id",
     "ALTER TABLE inventory_po_items ADD COLUMN IF NOT EXISTS fx_rate_id TEXT"),
    ("add_po_items_value_base",
     "ALTER TABLE inventory_po_items ADD COLUMN IF NOT EXISTS value_base NUMERIC(20, 2)"),

    # Header: how many costed foreign lines had no rate (the total_value of such
    # an order leaves them out, and says how many).
    ("add_po_log_fx_unconverted_lines",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS "
     "fx_unconverted_lines INT NOT NULL DEFAULT 0"),
]

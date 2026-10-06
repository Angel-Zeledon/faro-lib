"""Schema for the customer portal (the Rust routes in `backend-rs/src/routes/customer_portal.rs`).

A corporate customer who ordered months ahead opens a private link and sees ONLY
their own commitments, read-only. Three tables, all additive:

* `customer_portal_links` — one row per link: the credential (HASH only), the
  customer it is for, expiry and the revoked state. The customer is matched to
  `committed_demand.customer` by `customer_key` (lower-cased, trimmed).
* `customer_portal_promised_dates` — the date the tenant CHOSE to promise for one
  commitment. It is shown to the customer only when the link has `share_dates`.
* `customer_portal_events` — what the customer answered ('received' or 'the date
  does not work, with a comment'). Append-only; it never edits a commitment.

Rows are kept for good (permanent data); only whole-tenant erasure removes them
(`tenants/data_export.py`).
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("create_customer_portal_links",
     """CREATE TABLE IF NOT EXISTS customer_portal_links (
         id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         customer        TEXT NOT NULL,
         -- lower(btrim(customer)): the key commitments are matched on, computed
         -- by the database so both sides use the same case folding.
         customer_key    TEXT NOT NULL,
         -- SHA-256 of the 256-bit link token. The token itself is never stored,
         -- so a database leak cannot be turned into working links.
         token_hash      TEXT NOT NULL UNIQUE,
         -- 'es' | 'en': the language the page opens in.
         language        TEXT NOT NULL DEFAULT 'es' CHECK (language IN ('es', 'en')),
         -- Whether the customer sees the promised dates the tenant entered.
         share_dates     BOOLEAN NOT NULL DEFAULT FALSE,
         expires_at      TIMESTAMPTZ NOT NULL,
         revoked_at      TIMESTAMPTZ,
         revoked_by      TEXT,
         created_by      TEXT NOT NULL,
         created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         last_viewed_at  TIMESTAMPTZ,
         reopened_at     TIMESTAMPTZ,
         reopened_by     TEXT
     )"""),
    ("create_customer_portal_links_tenant_idx",
     "CREATE INDEX IF NOT EXISTS customer_portal_links_tenant_idx "
     "ON customer_portal_links (tenant_id, created_at DESC)"),
    ("create_customer_portal_promised_dates",
     """CREATE TABLE IF NOT EXISTS customer_portal_promised_dates (
         commitment_id  TEXT PRIMARY KEY REFERENCES committed_demand(id) ON DELETE CASCADE,
         tenant_id      TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         promised_date  DATE NOT NULL,
         set_by         TEXT NOT NULL,
         set_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_customer_portal_events",
     """CREATE TABLE IF NOT EXISTS customer_portal_events (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         link_id        TEXT NOT NULL REFERENCES customer_portal_links(id) ON DELETE CASCADE,
         commitment_id  TEXT NOT NULL,
         response       TEXT NOT NULL CHECK (response IN ('received', 'date_objection')),
         comment        TEXT,
         created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         -- Keyed hash of the address: enough to tell two answers apart, not
         -- enough to identify a person.
         ip_hash        TEXT
     )"""),
    ("create_customer_portal_events_idx",
     "CREATE INDEX IF NOT EXISTS customer_portal_events_link_idx "
     "ON customer_portal_events (tenant_id, link_id, created_at DESC)"),
]

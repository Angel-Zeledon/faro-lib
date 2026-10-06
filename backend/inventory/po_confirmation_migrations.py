"""Schema for the supplier confirmation link (see `po_confirmation_service.py`).

Three tables, and none of them is ever updated in place where history matters:

* `po_confirmation_requests` — one row per (order, supplier): the credential
  (HASH only), when it expires, and the lock state.
* `po_line_confirmations` — what the supplier answered, one row per line per
  submission. Append-only: a second submission adds revision 2 beside revision 1.
* `po_confirmation_acceptances` — the buyer accepting a proposed change, kept in
  its own insert-only table so the answer rows stay immutable.

The old rows are kept for good (permanent data); only whole-tenant erasure
removes them (`tenants/data_export.py`).
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("create_po_confirmation_requests",
     """CREATE TABLE IF NOT EXISTS po_confirmation_requests (
         id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id        TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         po_log_id        TEXT NOT NULL REFERENCES inventory_po_log(id) ON DELETE CASCADE,
         supplier         TEXT NOT NULL,
         -- SHA-256 of the 256-bit link token. The token itself is never stored,
         -- so a database leak cannot be turned into working links.
         token_hash       TEXT NOT NULL UNIQUE,
         -- The date the supplier is asked to meet (what the buyer expects).
         requested_date   DATE,
         -- 'es' | 'en': the language the page opens in.
         language         TEXT NOT NULL DEFAULT 'es',
         expires_at       TIMESTAMPTZ NOT NULL,
         revoked_at       TIMESTAMPTZ,
         revoked_by       TEXT,
         created_by       TEXT NOT NULL,
         created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         token_issued_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         last_viewed_at   TIMESTAMPTZ,
         submitted_at     TIMESTAMPTZ,
         reopened_at      TIMESTAMPTZ,
         reopened_by      TEXT,
         UNIQUE (tenant_id, po_log_id, supplier)
     )"""),
    ("create_po_confirmation_requests_po_idx",
     "CREATE INDEX IF NOT EXISTS po_confirmation_requests_po_idx "
     "ON po_confirmation_requests (tenant_id, po_log_id)"),
    ("create_po_line_confirmations",
     """CREATE TABLE IF NOT EXISTS po_line_confirmations (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         request_id     TEXT NOT NULL REFERENCES po_confirmation_requests(id) ON DELETE CASCADE,
         po_log_id      TEXT NOT NULL,
         po_item_id     TEXT NOT NULL,
         revision       INT  NOT NULL CHECK (revision >= 1),
         -- NULL when the line was declined.
         confirmed_qty  DOUBLE PRECISION CHECK (confirmed_qty IS NULL OR confirmed_qty > 0),
         promised_date  DATE,
         status         TEXT NOT NULL CHECK (status IN ('confirmed', 'changed', 'declined')),
         note           TEXT,
         submission_id  TEXT NOT NULL,
         submitted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         -- Keyed hash of the address and a truncated user agent: enough to tell
         -- two submissions apart, not enough to identify a person.
         ip_hash        TEXT,
         user_agent     TEXT,
         UNIQUE (request_id, po_item_id, revision)
     )"""),
    ("create_po_line_confirmations_idx",
     "CREATE INDEX IF NOT EXISTS po_line_confirmations_po_idx "
     "ON po_line_confirmations (tenant_id, po_log_id, po_item_id, revision DESC)"),
    ("create_po_confirmation_acceptances",
     """CREATE TABLE IF NOT EXISTS po_confirmation_acceptances (
         confirmation_id  TEXT PRIMARY KEY REFERENCES po_line_confirmations(id) ON DELETE CASCADE,
         tenant_id        TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         po_log_id        TEXT NOT NULL,
         accepted_by      TEXT NOT NULL,
         accepted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_po_confirmation_acceptances_idx",
     "CREATE INDEX IF NOT EXISTS po_confirmation_acceptances_po_idx "
     "ON po_confirmation_acceptances (tenant_id, po_log_id)"),
]

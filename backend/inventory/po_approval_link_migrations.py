"""Schema for deciding a purchase-order approval from a message
(see `po_approval_link_service.py`).

* `po_approval_links`: one row per (approval request, approver, channel): the
  credential (HASH only), when it expires, and whether it was used or revoked.
  A link is a bearer credential for ONE decision by ONE person on ONE order, so
  the row binds the approver, the approval request and the order, and the
  scope of what the link may do (today only `decide`).
* `po_approvals.decided_channel`: how the decision was taken. NULL = in the app
  (every row that exists today), `message` = through a decision link.

Additive. An installation that never issues a link has an empty table and a
NULL column everywhere: every reader behaves exactly as before.

The rows are kept for good (permanent data, like every approval record); only
whole-tenant erasure removes them, through the tenant cascade.
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("create_po_approval_links",
     """CREATE TABLE IF NOT EXISTS po_approval_links (
         id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         po_log_id     TEXT NOT NULL REFERENCES inventory_po_log(id) ON DELETE CASCADE,
         approval_id   TEXT NOT NULL REFERENCES po_approvals(id) ON DELETE CASCADE,
         approver_id   TEXT NOT NULL,
         -- SHA-256 of the 256-bit link token. The token itself is never stored,
         -- so a database leak cannot be turned into working links.
         token_hash    TEXT NOT NULL UNIQUE,
         scope         TEXT NOT NULL DEFAULT 'decide' CHECK (scope IN ('decide')),
         channel       TEXT NOT NULL CHECK (channel IN ('email', 'whatsapp')),
         expires_at    TIMESTAMPTZ NOT NULL,
         created_by    TEXT NOT NULL,
         created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         issued_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         last_viewed_at TIMESTAMPTZ,
         used_at       TIMESTAMPTZ,
         used_decision TEXT CHECK (used_decision IN ('approved', 'rejected')),
         revoked_at    TIMESTAMPTZ,
         revoked_by    TEXT,
         revoked_reason TEXT,
         UNIQUE (approval_id, approver_id, channel)
     )"""),
    ("create_po_approval_links_po_idx",
     "CREATE INDEX IF NOT EXISTS po_approval_links_po_idx "
     "ON po_approval_links (tenant_id, po_log_id)"),
    ("add_po_approvals_decided_channel",
     "ALTER TABLE po_approvals ADD COLUMN IF NOT EXISTS decided_channel TEXT"),
]

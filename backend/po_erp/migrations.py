"""Schema for the purchase-order feed to the ERP (see `service.py`).

* `suppliers.erp_code`: the vendor number the company's ERP uses for the supplier.
  NULL until the company maps it; an order for an unmapped supplier is still in
  the feed, flagged, never silently dropped.
* `inventory_po_log.erp_feed_seq` / `erp_feed_at`: the feed cursor. A row trigger
  stamps them from one sequence every time a column the ERP cares about changes
  (a new order, its approval state, a cancellation, its reception state, the ERP
  reference). The cursor therefore lives in the database and cannot be bypassed
  by a code path that forgets to bump it, the same reasoning as
  `status_input_bumps`. The reader holds back rows younger than a few seconds so
  a transaction that took a lower number but commits later is not skipped.
* `po_erp_references`: the ERP's own document number, one per (order, supplier)
  because one order can span several suppliers and the ERP raises one purchase
  order per vendor. Written once; a different number later is refused, not
  overwritten. Writing one stamps `inventory_po_log.erp_reference_at`, which moves
  the order's feed cursor so the feed shows it acknowledged.

Additive. A company that never reads the feed has NULL columns and an unused
sequence: every other reader behaves exactly as before.
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("add_suppliers_erp_code",
     "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS erp_code TEXT"),
    ("create_po_erp_feed_seq",
     "CREATE SEQUENCE IF NOT EXISTS po_erp_feed_seq"),
    ("add_po_log_erp_feed_seq",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS erp_feed_seq BIGINT"),
    ("add_po_log_erp_feed_at",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS erp_feed_at TIMESTAMPTZ"),
    ("add_po_log_erp_reference_at",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS erp_reference_at TIMESTAMPTZ"),
    ("create_po_erp_references",
     """CREATE TABLE IF NOT EXISTS po_erp_references (
         id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         po_log_id     TEXT NOT NULL REFERENCES inventory_po_log(id) ON DELETE CASCADE,
         -- The supplier id the lines carry, or '' for lines with no supplier.
         supplier_key  TEXT NOT NULL DEFAULT '',
         reference     TEXT NOT NULL,
         created_by    TEXT,
         created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         UNIQUE (po_log_id, supplier_key)
     )"""),
    ("create_po_erp_references_idx",
     "CREATE INDEX IF NOT EXISTS po_erp_references_tenant_idx ON po_erp_references (tenant_id, reference)"),
    ("create_po_erp_feed_touch",
     """CREATE OR REPLACE FUNCTION po_erp_feed_touch() RETURNS trigger AS $$
        BEGIN
            NEW.erp_feed_seq := nextval('po_erp_feed_seq');
            NEW.erp_feed_at := clock_timestamp();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""),
    ("drop_po_erp_feed_trigger",
     "DROP TRIGGER IF EXISTS po_erp_feed_trigger ON inventory_po_log"),
    ("create_po_erp_feed_trigger",
     """CREATE TRIGGER po_erp_feed_trigger
            BEFORE INSERT OR UPDATE OF approval_status, approved_amount, cancelled_at,
                                       reception_status, erp_reference_at
            ON inventory_po_log
            FOR EACH ROW EXECUTE FUNCTION po_erp_feed_touch()"""),
    # Orders that existed before the feed get a number in creation order, once.
    ("backfill_po_erp_feed",
     """UPDATE inventory_po_log l
           SET erp_feed_seq = n.seq, erp_feed_at = l.generated_at
          FROM (SELECT id, nextval('po_erp_feed_seq') AS seq
                  FROM (SELECT id FROM inventory_po_log
                         WHERE erp_feed_seq IS NULL ORDER BY generated_at, id) o) n
         WHERE l.id = n.id AND l.erp_feed_seq IS NULL"""),
    ("create_po_erp_feed_idx",
     "CREATE INDEX IF NOT EXISTS po_log_erp_feed_idx ON inventory_po_log (tenant_id, erp_feed_seq)"),
]

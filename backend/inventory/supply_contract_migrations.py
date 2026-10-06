"""Schema for blanket supply contracts (see `supply_contract_service.py`)."""

MIGRATIONS: list[tuple[str, str]] = [
    # A blanket contract ("contrato marco"): a customer agrees to take a volume
    # of one or more SKUs over a period, called off in releases. The ledger is
    # APPEND-ONLY: every change (a revision, activating a draft, closing,
    # cancelling) is a NEW row of the same lineage (`root_id`) with the next
    # `revision`; the row it replaces is stamped `superseded_by`/`superseded_at`
    # and is otherwise never touched. Nothing here is ever deleted except by
    # whole-tenant erasure.
    #
    # `lines`    [{sku, total_quantity, unit_price|null}]   one entry per SKU
    # `releases` explicit schedule only: [{sku, date, quantity}]; NULL when the
    #            schedule is an even monthly / weekly split of each line.
    ("create_supply_contracts",
     """CREATE TABLE IF NOT EXISTS supply_contracts (
         id              TEXT PRIMARY KEY,
         tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         root_id         TEXT NOT NULL,
         revision        INT NOT NULL CHECK (revision >= 1),
         customer        TEXT NOT NULL,
         reference       TEXT,
         lines           JSONB NOT NULL,
         period_start    DATE NOT NULL,
         period_end      DATE NOT NULL,
         schedule_kind   TEXT NOT NULL
                         CHECK (schedule_kind IN ('monthly', 'weekly', 'explicit')),
         releases        JSONB,
         tolerance_pct   DOUBLE PRECISION NOT NULL DEFAULT 0
                         CHECK (tolerance_pct >= 0 AND tolerance_pct <= 100),
         status          TEXT NOT NULL
                         CHECK (status IN ('draft', 'active', 'closed', 'cancelled')),
         warehouse_id    TEXT,
         on_top_of_base  BOOLEAN NOT NULL DEFAULT TRUE,
         note            TEXT,
         created_by      TEXT NOT NULL,
         created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         superseded_by   TEXT,
         superseded_at   TIMESTAMPTZ,
         CHECK (period_end >= period_start),
         UNIQUE (tenant_id, root_id, revision)
     )"""),
    ("create_supply_contracts_tenant_idx",
     "CREATE INDEX IF NOT EXISTS supply_contracts_tenant_idx "
     "ON supply_contracts (tenant_id, status) WHERE superseded_by IS NULL"),
    # Exactly one current revision per lineage: two simultaneous revisions of
    # the same contract cannot both win.
    ("create_supply_contracts_current_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS supply_contracts_current_uniq "
     "ON supply_contracts (tenant_id, root_id) WHERE superseded_by IS NULL"),

    # ── Commitments materialised from a contract ─────────────────────────────
    # `source` says where a commitment came from: a person ('manual', every row
    # that existed before contracts) or a contract release ('contract').
    ("add_committed_demand_source",
     "ALTER TABLE committed_demand ADD COLUMN IF NOT EXISTS source TEXT NOT NULL "
     "DEFAULT 'manual'"),
    ("add_committed_demand_source_check",
     """DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint
                          WHERE conname = 'committed_demand_source_check') THEN
            ALTER TABLE committed_demand ADD CONSTRAINT committed_demand_source_check
              CHECK (source IN ('manual', 'contract'));
          END IF;
        END $$"""),
    # The contract REVISION that materialised the row, its lineage, and the
    # release date it stands for. `contract_release_date` is the idempotency
    # key and never changes, even when a person moves `delivery_date`.
    ("add_committed_demand_contract_id",
     "ALTER TABLE committed_demand ADD COLUMN IF NOT EXISTS contract_id TEXT"),
    ("add_committed_demand_contract_root_id",
     "ALTER TABLE committed_demand ADD COLUMN IF NOT EXISTS contract_root_id TEXT"),
    ("add_committed_demand_contract_release_date",
     "ALTER TABLE committed_demand ADD COLUMN IF NOT EXISTS contract_release_date DATE"),
    # Stamped when a revision, closing or cancelling of the contract withdrew
    # the (still open) commitment. A withdrawn row is history: it no longer
    # holds its release's slot, so the new revision can materialise it again.
    ("add_committed_demand_contract_withdrawn_at",
     "ALTER TABLE committed_demand ADD COLUMN IF NOT EXISTS contract_withdrawn_at TIMESTAMPTZ"),
    # The idempotency wall: one live commitment per contract line (SKU) per
    # release date. A re-run of the materialisation, or the daily pass racing a
    # save, inserts with ON CONFLICT DO NOTHING against this index.
    ("create_committed_demand_contract_release_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS committed_demand_contract_release_uniq "
     "ON committed_demand (tenant_id, contract_root_id, sku, contract_release_date) "
     "WHERE contract_root_id IS NOT NULL AND contract_withdrawn_at IS NULL"),

    # ── Renewal and expiry tracking (see `contract_renewal.py`) ──────────────
    # `notice_days`: days before `period_end` by which a person must say
    #   whether the contract renews (NULL = no notice period recorded).
    # `auto_renew`: the contract renews by itself unless somebody gives notice.
    #   A LABEL for the screens and the alert wording: nothing clones a contract
    #   on its own.
    # `renewal_lead_days`: alert lead times; NULL = nobody chose, the product
    #   default 60/30/7 applies (a default must not look like a choice).
    # `renewed_from_root_id`: set on a contract created by the renew action, the
    #   lineage it renews.
    ("add_supply_contracts_notice_days",
     "ALTER TABLE supply_contracts ADD COLUMN IF NOT EXISTS notice_days INT"),
    ("add_supply_contracts_notice_days_check",
     """DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint
                          WHERE conname = 'supply_contracts_notice_days_check') THEN
            ALTER TABLE supply_contracts ADD CONSTRAINT supply_contracts_notice_days_check
              CHECK (notice_days IS NULL OR (notice_days >= 0 AND notice_days <= 730));
          END IF;
        END $$"""),
    ("add_supply_contracts_auto_renew",
     "ALTER TABLE supply_contracts ADD COLUMN IF NOT EXISTS auto_renew BOOLEAN "
     "NOT NULL DEFAULT FALSE"),
    ("add_supply_contracts_renewal_lead_days",
     "ALTER TABLE supply_contracts ADD COLUMN IF NOT EXISTS renewal_lead_days INT[]"),
    ("add_supply_contracts_renewed_from_root_id",
     "ALTER TABLE supply_contracts ADD COLUMN IF NOT EXISTS renewed_from_root_id TEXT"),
    # Not unique on purpose: a renewal that was cancelled frees the contract to
    # be renewed again. Two people pressing Renew at once are serialised by the
    # row lock the renew action takes on the contract's current revision.
    ("create_supply_contracts_renewed_from_idx",
     "CREATE INDEX IF NOT EXISTS supply_contracts_renewed_from_idx "
     "ON supply_contracts (tenant_id, renewed_from_root_id) "
     "WHERE renewed_from_root_id IS NOT NULL"),
]

"""Schema for spike exclusions (see `spike_edit_service.py`)."""

MIGRATIONS: list[tuple[str, str]] = [
    # The ledger. Beside the uploaded data, never inside it: the original file is
    # untouched and a run applies these on its in-memory copy. It is keyed by the
    # DATASET (not by a session), because "March 2025 was a one-off" is a fact
    # about the history and must apply to every later training on that data.
    #
    # Append-only: a mark is never edited and never deleted. Undoing one stamps
    # `reverted_by`/`reverted_at` on it, so "who excluded this, why, and who
    # restored it" stays answerable. Marking the period again later is a new row.
    ("create_spike_edits",
     """CREATE TABLE IF NOT EXISTS spike_edits (
         id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         dataset_id   TEXT NOT NULL,
         sku          TEXT NOT NULL,
         start_date   DATE NOT NULL,
         end_date     DATE NOT NULL,
         reason_code  TEXT NOT NULL,
         reason_note  TEXT,
         created_by   TEXT NOT NULL,
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         reverted_by  TEXT,
         reverted_at  TIMESTAMPTZ,
         CHECK (end_date >= start_date)
     )"""),
    ("create_spike_edits_idx",
     "CREATE INDEX IF NOT EXISTS spike_edits_dataset_idx "
     "ON spike_edits (tenant_id, dataset_id, sku)"),

    # What each run did with the ledger: one row per mark per training, written
    # by the runner. Insert-only. `points_treated = 0` is recorded too (a mark
    # whose period matched no data), so the lineage never hides it.
    ("create_spike_edit_applications",
     """CREATE TABLE IF NOT EXISTS spike_edit_applications (
         id                 TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id          TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         session_id         TEXT NOT NULL,
         spike_edit_id      TEXT NOT NULL,
         sku                TEXT NOT NULL,
         status             TEXT NOT NULL,
         points_treated     INTEGER NOT NULL,
         original_total     DOUBLE PRECISION NOT NULL,
         replacement_total  DOUBLE PRECISION NOT NULL,
         applied_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_spike_edit_applications_idx",
     "CREATE INDEX IF NOT EXISTS spike_edit_applications_session_idx "
     "ON spike_edit_applications (tenant_id, session_id, sku)"),
]

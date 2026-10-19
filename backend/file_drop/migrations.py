"""Schema for file-drop sources (see `service.py`).

* `file_drop_sources`: one SFTP directory or S3-compatible bucket/prefix a
  company drops files into. Credentials are stored encrypted (`secrets`) and are
  never returned. `host_key_fingerprint` is the SFTP server's pinned key (trust
  on first use: shown to an admin, a change is refused until an admin accepts it).
  `observations` is what the last look saw ({name: [size, mtime]}); a file is
  read only once two looks agree, so a file still being uploaded is not read.
* `file_drop_files`: the ledger. One row per (source, name, content hash): the
  same bytes are never applied twice, and every decision (applied, held for a
  person, rejected, failed) keeps its reason code.

Additive. A company with no source has two empty tables: nothing else reads them.
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("create_file_drop_sources",
     """CREATE TABLE IF NOT EXISTS file_drop_sources (
         id                   TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id            TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         name                 TEXT NOT NULL,
         kind                 TEXT NOT NULL CHECK (kind IN ('sftp', 's3')),
         config               JSONB NOT NULL DEFAULT '{}'::jsonb,
         secrets              TEXT,
         host_key_fingerprint TEXT,
         host_key_pinned_at   TIMESTAMPTZ,
         target_dataset_id    TEXT,
         file_pattern         TEXT NOT NULL DEFAULT '*.csv',
         interval_minutes     INT NOT NULL DEFAULT 15 CHECK (interval_minutes >= 5),
         min_age_seconds      INT NOT NULL DEFAULT 60 CHECK (min_age_seconds >= 0),
         enabled              BOOLEAN NOT NULL DEFAULT TRUE,
         status               TEXT NOT NULL DEFAULT 'new',
         last_checked_at      TIMESTAMPTZ,
         last_ok_at           TIMESTAMPTZ,
         last_error_code      TEXT,
         last_error_params    JSONB,
         observations         JSONB NOT NULL DEFAULT '{}'::jsonb,
         next_run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         lease_until          TIMESTAMPTZ,
         created_by           TEXT,
         created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         UNIQUE (tenant_id, name)
     )"""),
    ("create_file_drop_sources_due_idx",
     "CREATE INDEX IF NOT EXISTS file_drop_sources_due_idx ON file_drop_sources (next_run_at) WHERE enabled"),
    ("create_file_drop_files",
     """CREATE TABLE IF NOT EXISTS file_drop_files (
         id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         source_id     TEXT NOT NULL REFERENCES file_drop_sources(id) ON DELETE CASCADE,
         remote_name   TEXT NOT NULL,
         size_bytes    BIGINT NOT NULL DEFAULT 0,
         content_hash  TEXT NOT NULL,
         status        TEXT NOT NULL CHECK (status IN ('applying', 'applied', 'held', 'rejected', 'failed')),
         reason_code   TEXT,
         reason_params JSONB,
         rows_count    INT,
         attempts      INT NOT NULL DEFAULT 1,
         moved         BOOLEAN NOT NULL DEFAULT FALSE,
         dataset_id    TEXT,
         seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         decided_at    TIMESTAMPTZ,
         decided_by    TEXT,
         UNIQUE (source_id, remote_name, content_hash)
     )"""),
    ("create_file_drop_files_idx",
     "CREATE INDEX IF NOT EXISTS file_drop_files_source_idx ON file_drop_files (source_id, seen_at DESC)"),
]

"""`tenants.sandbox_of`: the company that asked for a sandbox tenant (NULL for every
real tenant and every ordinary trial). Additive; nothing else reads it except the
sandbox module."""

MIGRATIONS: list[tuple[str, str]] = [
    ("add_tenants_sandbox_of",
     "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS sandbox_of TEXT"),
    ("create_tenants_sandbox_of_idx",
     "CREATE INDEX IF NOT EXISTS tenants_sandbox_of_idx ON tenants (sandbox_of) WHERE sandbox_of IS NOT NULL"),
]

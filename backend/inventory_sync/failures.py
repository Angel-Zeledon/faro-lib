"""Every way a sync can go wrong, and what happens next. Pure: no DB.

Each outcome code a run can end with maps to:

* a **category** — what kind of problem it is;
* the **health** the profile moves to;
* the **stage** it happened in (connect / read / validate / apply / ours);
* whether the scheduler **retries** it on its own (with backoff), or waits
  for a person (a wrong password is never retried against the customer's
  server: it would lock the account);
* a **remedy** — the frontend renders `isync.remedy.<code>` (falling back to
  `isync.remedy.category.<category>`), so every outcome tells the reader what
  to do next. The backend never writes the sentence.

Categories and the health they set:

    transient   degraded  the source or the network hiccupped; retried with backoff
    customer    blocked   needs a change on the customer's side (password, grants,
                          firewall, TLS); not retried until the source is edited or
                          a person retries
    schema      paused    the source no longer has what the mapping reads
                          ("mapping broken", with the column named); until edited
    safety      paused    the safety brake; never unfreezes without a person
    config      blocked   the profile asks for something impossible (too many rows,
                          an unusable watermark); until edited
    capacity    degraded  the run worked but refused new SKUs (plan ceiling)
    ours        degraded  StockAI's own side failed (database busy, disk); retried
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

TRANSIENT, CUSTOMER, SCHEMA, SAFETY, CONFIG, CAPACITY, OURS = (
    "transient", "customer", "schema", "safety", "config", "capacity", "ours")

HEALTH_OF = {
    TRANSIENT: "degraded", CUSTOMER: "blocked", SCHEMA: "paused", SAFETY: "paused",
    CONFIG: "blocked", CAPACITY: "degraded", OURS: "degraded",
}
RETRIES = {TRANSIENT: True, OURS: True, CAPACITY: True,
           CUSTOMER: False, SCHEMA: False, SAFETY: False, CONFIG: False}

# Codes whose remedy should name StockAI's egress addresses (a firewall or an
# IP allowlist on the customer's side is the usual cause).
NETWORK_CODES = frozenset({
    "data_source_connect_timeout", "data_source_host_unreachable", "data_source_host_rejected",
})


@dataclass(frozen=True)
class Outcome:
    code: str
    category: str
    stage: str

    @property
    def health(self) -> str:
        return HEALTH_OF[self.category]

    @property
    def retries(self) -> bool:
        return RETRIES[self.category]

    def as_dict(self) -> dict:
        return {"code": self.code, "category": self.category, "stage": self.stage,
                "health": self.health, "retries": self.retries,
                "remedy_key": f"isync.remedy.{self.code}",
                "egress_hint": self.code in NETWORK_CODES}


_C = Outcome
CATALOGUE: dict[str, Outcome] = {o.code: o for o in (
    # connection
    _C("data_source_dns_failed", CUSTOMER, "connect"),
    _C("data_source_host_not_allowed", CUSTOMER, "connect"),
    _C("data_source_host_forbidden", CUSTOMER, "connect"),
    _C("data_source_host_unreachable", TRANSIENT, "connect"),
    _C("data_source_connect_timeout", TRANSIENT, "connect"),
    _C("data_source_host_rejected", CUSTOMER, "connect"),
    _C("data_source_tls_failed", CUSTOMER, "connect"),
    _C("data_source_tls_cert_expired", CUSTOMER, "connect"),
    _C("data_source_auth_failed", CUSTOMER, "connect"),
    _C("data_source_account_locked", CUSTOMER, "connect"),
    _C("data_source_password_expired", CUSTOMER, "connect"),
    _C("data_source_credentials_unreadable", CUSTOMER, "connect"),
    _C("data_source_database_not_found", CUSTOMER, "connect"),
    _C("data_source_permission_denied", CUSTOMER, "read"),
    _C("data_source_server_busy", TRANSIENT, "connect"),
    _C("data_source_connection_lost", TRANSIENT, "read"),
    _C("data_source_busy", TRANSIENT, "connect"),
    _C("data_source_query_timeout", TRANSIENT, "read"),
    _C("data_source_connect_failed", TRANSIENT, "connect"),
    _C("data_source_driver_missing", OURS, "connect"),
    _C("data_source_not_connected", CUSTOMER, "connect"),
    _C("data_source_not_found", CONFIG, "connect"),
    _C("data_source_startup_options_refused", CUSTOMER, "connect"),
    _C("data_source_read_only_violation", CONFIG, "read"),
    # schema drift
    _C("inventory_sync_column_missing", SCHEMA, "read"),
    _C("inventory_sync_object_missing", SCHEMA, "read"),
    _C("sql_query_failed", SCHEMA, "read"),
    _C("mongo_query_failed", SCHEMA, "read"),
    _C("mongo_query_invalid", CONFIG, "read"),
    _C("mongo_stage_not_allowed", CONFIG, "read"),
    _C("mongo_query_forbidden_operator", CONFIG, "read"),
    # configuration
    _C("inventory_sync_too_many_rows", CONFIG, "read"),
    _C("inventory_sync_watermark_unsupported", CONFIG, "read"),
    _C("inventory_sync_strategy_unavailable", CONFIG, "read"),
    _C("inventory_sync_cell_too_large", CONFIG, "validate"),
    _C("inventory_sync_profile_changed", TRANSIENT, "ours"),
    # safety brake
    _C("brake_source_empty", SAFETY, "validate"),
    _C("brake_too_many_rejected", SAFETY, "validate"),
    _C("brake_no_valid_rows", SAFETY, "validate"),
    _C("brake_skus_vanished", SAFETY, "validate"),
    _C("brake_row_count_drop", SAFETY, "validate"),
    _C("brake_mass_quantity_change", SAFETY, "validate"),
    _C("brake_source_looks_different", SAFETY, "validate"),
    _C("brake_ledger_drift", SAFETY, "validate"),
    # capacity (the run still succeeds for what fits)
    _C("inventory_sync_sku_ceiling", CAPACITY, "apply"),
    _C("inventory_sync_location_ceiling", CAPACITY, "apply"),
    # advice the doctor gives (never a run outcome by itself)
    _C("inventory_sync_filter_not_indexed", CONFIG, "read"),
    _C("inventory_sync_capture_fallback", TRANSIENT, "read"),
    _C("inventory_sync_future_timestamps", CONFIG, "read"),
    # our side
    _C("inventory_sync_our_db_busy", OURS, "apply"),
    _C("inventory_sync_internal_error", OURS, "ours"),
    _C("inventory_sync_interrupted", OURS, "ours"),
    _C("inventory_sync_storage_full", OURS, "ours"),
)}


def outcome_of(code: Optional[str]) -> Outcome:
    """The outcome for a code; an unknown code is treated as ours (retried,
    and logged), never silently as success."""
    if not code:
        return Outcome("inventory_sync_internal_error", OURS, "ours")
    if code in CATALOGUE:
        return CATALOGUE[code]
    if code.startswith("brake_"):
        return Outcome(code, SAFETY, "validate")
    if code.startswith("plan_limit") or code == "PLAN_LIMIT_REACHED":
        return Outcome(code, CAPACITY, "apply")
    return Outcome(code, OURS, "ours")


# ── Reading a raw failure as schema drift ─────────────────────────────────────
# A saved query, a dropped column or a dropped table fails with the engine's
# own words; these patterns name the missing object so the screen can say
# "column LABST no longer exists" instead of "the query failed".

_COLUMN_PATTERNS = (
    re.compile(r'column "?([\w.$#]+)"? does not exist', re.I),             # PostgreSQL
    re.compile(r"Unknown column '([^']+)'", re.I),                           # MySQL
    re.compile(r"Invalid column name '([^']+)'", re.I),                      # SQL Server
    re.compile(r'ORA-00904:\s*"?([^":\s]+)"?', re.I),                        # Oracle
)
_OBJECT_PATTERNS = (
    re.compile(r'relation "?([\w.$#]+)"? does not exist', re.I),           # PostgreSQL
    re.compile(r"Table '([^']+)' doesn't exist", re.I),                      # MySQL
    re.compile(r"Invalid object name '([^']+)'", re.I),                      # SQL Server
    re.compile(r"ORA-00942", re.I),                                          # Oracle
    re.compile(r"ORA-04063", re.I),                                          # Oracle broken view
    re.compile(r"references invalid table|view .* is invalid", re.I),        # broken views
)
_LOCKED = re.compile(r"ORA-28000|account is locked|Account is locked|\b3118\b|18486", re.I)
_EXPIRED_PW = re.compile(r"ORA-28001|password has expired|\b1862\b|18487|18488|password expired", re.I)
_CERT_EXPIRED = re.compile(r"certificate has expired|certificate expired|CERT_HAS_EXPIRED", re.I)


def refine(code: str, params: dict) -> tuple[str, dict]:
    """A more precise code for a generic failure, when its text says more."""
    reason = str((params or {}).get("reason") or "")
    if code in ("sql_query_failed", "mongo_query_failed", "data_source_permission_denied"):
        for pat in _COLUMN_PATTERNS:
            m = pat.search(reason)
            if m:
                return "inventory_sync_column_missing", {**params, "column": m.group(1)}
        for pat in _OBJECT_PATTERNS:
            m = pat.search(reason)
            if m:
                obj = m.group(1) if m.groups() else ""
                return "inventory_sync_object_missing", {**params, "object": obj}
    if code in ("data_source_auth_failed", "data_source_connect_failed"):
        if _LOCKED.search(reason):
            return "data_source_account_locked", params
        if _EXPIRED_PW.search(reason):
            return "data_source_password_expired", params
    if code == "data_source_tls_failed" and _CERT_EXPIRED.search(reason):
        return "data_source_tls_cert_expired", params
    return code, params


def public_params(params: Any) -> dict:
    """Params safe to store on a run row and show: scalars only, capped."""
    out = {}
    for k, v in (params or {}).items():
        if isinstance(v, (int, float, bool)) or v is None:
            out[str(k)[:40]] = v
        else:
            out[str(k)[:40]] = str(v)[:300]
    return out


# ── Documented limits ─────────────────────────────────────────────────────────
# Failure modes this package does NOT handle in code, on purpose, with the
# reason and what to do instead. The screen and GET /inventory-sync/fields
# show them; `test_inventory_sync_failure_modes.py` keeps the list honest.
LIMITS: dict[str, str] = {
    "read_replica_lag": "A lagging read replica returns older stock; nothing in a generic connection says how far behind it is. The row-count and quantity-jump brakes catch gross cases; point the source at the primary or a replica with bounded lag.",
    "postgres_logical_replication": "Pulling a logical replication slot from StockAI would hold WAL on the customer's server if StockAI stopped reading; use the push path with Debezium or a trigger outbox instead.",
    "mysql_binlog": "Reading the binlog needs REPLICATION SLAVE/CLIENT, not a read-only grant; use the push path (Debezium / Maxwell forwarder) instead.",
    "postgres_xmin_cursor": "xmin is a 32-bit transaction id that wraps around; an updated_at watermark or the push path is used instead.",
    "sqlserver_dirty_reads": "NOLOCK / READ UNCOMMITTED can read rows that are later rolled back: wrong stock. Reads use the default isolation; enable READ_COMMITTED_SNAPSHOT on the customer's database for lock-free consistent reads.",
    "mongodb_multi_host": "One host is connected directly (no replica-set discovery), so every address passes the SSRF check; for failover, point the source at the current primary or a hidden secondary.",
    "negative_stock_kept": "inventory_stock cannot hold a negative quantity; a negative balance is rejected (default) or clamped to 0 and counted.",
    "resumable_read": "A crash while READING restarts the read (nothing was applied); a crash while APPLYING resumes from the checkpoint.",
    "deletes_between_reconciles": "Incremental strategies see changes, not deletions of the whole row; deletions settle at the next full reconcile.",
    "full_read_holds_snapshot": "A full read is one consistent statement: it takes no locks but keeps one read snapshot open while it streams (measured: about a minute for 1M rows on MySQL). Splitting it into pages would release the snapshot but read a moving table; schedule full reads off-peak and use incremental or continuous in between.",
}

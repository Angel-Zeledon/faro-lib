"""The sync profile store: create, read, update, delete, enable, disable.

Every refusal is an `AppError` with a stable code; the frontend renders the
sentence (`errors.<code>`).

Two rules worth knowing:

* **A profile is enabled only after a dry run of its CURRENT configuration.**
  `config_hash` fingerprints everything that changes what a run would write;
  the dry run stamps `dry_run_hash`. Enabling with the two different is
  refused (`inventory_sync_dry_run_required`), and editing an enabled
  profile's configuration turns it off until it is previewed again — the
  response says so (`disabled_by_edit`).
* **Nothing here touches stock.** Deleting a profile removes its schedule,
  run log and key ledger; the inventory it synced stays exactly as it is.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.config import settings
from backend.db.connection import _json, execute, query, query_one
from backend.errors import AppError
from backend.inventory_sync import fields as F
from backend.utils.ids import generate_id

import re

SOURCE_KINDS = ("table", "query", "collection", "pipeline", "push")
SQL_KINDS = ("table", "query")
MONGO_KINDS = ("collection", "pipeline")
MODES = ("full", "incremental")
MISSING_POLICIES = ("ignore", "mark_inactive", "zero")
NEGATIVE_POLICIES = ("reject", "zero")
DUPLICATE_POLICIES = ("reject", "sum")
FRESHNESS = ("scheduled", "continuous", "on_demand")
MAX_INTERVAL_MINUTES = 7 * 24 * 60
MAX_NAME = 120
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
# Strategies that only exist on one engine.
_ENGINE_STRATEGY = {"change_tracking": "mssql", "oracle_rowscn": "oracle", "change_stream": "mongodb"}

# Fields whose change alters what a run writes (and therefore needs a new
# dry run before the profile runs on its own again).
_HASHED = (
    "data_source_id", "source_kind", "source_schema", "source_object", "source_query",
    "mapping", "filters", "default_warehouse", "mode", "watermark_column",
    "missing_policy", "negative_policy", "duplicate_policy", "allow_new_warehouses",
    "stock_model", "model_config", "strategy", "priority",
)
_EDITABLE = _HASHED + (
    "name", "overlap_minutes", "interval_minutes", "brake_max_vanished_pct",
    "brake_min_row_ratio", "brake_max_rejected_pct", "brake_max_jump_pct", "slug",
    "freshness", "poll_seconds", "reconcile_minutes",
)
# The stored columns a create / update writes (everything normalize returns
# except `engine`, which is derived).
_COLUMNS = (
    "data_source_id", "name", "source_kind", "source_schema", "source_object", "source_query",
    "mapping", "filters", "default_warehouse", "mode", "watermark_column", "overlap_minutes",
    "interval_minutes", "missing_policy", "negative_policy", "duplicate_policy",
    "allow_new_warehouses", "brake_max_vanished_pct", "brake_min_row_ratio",
    "brake_max_rejected_pct", "brake_max_jump_pct", "slug", "priority", "stock_model",
    "model_config", "freshness", "poll_seconds", "reconcile_minutes", "strategy",
)
_JSON_COLUMNS = ("mapping", "filters", "model_config")


def _reject(code: str, message: str, status: int = 422, **params: Any) -> AppError:
    return AppError(code, message, status_code=status, params=params)


def _not_found(profile_id: str) -> AppError:
    return _reject("inventory_sync_profile_not_found", "Sync profile not found.", 404,
                   profile_id=profile_id)


def config_hash(cfg: dict) -> str:
    raw = json.dumps({k: cfg.get(k) for k in _HASHED}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def min_interval() -> int:
    return max(1, int(getattr(settings, "inventory_sync_min_interval_minutes", 5) or 5))


def min_poll() -> int:
    return max(5, int(getattr(settings, "inventory_sync_min_poll_seconds", 15) or 15))


def max_profiles() -> int:
    return max(1, int(getattr(settings, "inventory_sync_max_profiles_per_tenant", 20) or 20))


def _source(tenant_id: str, source_id: str) -> dict:
    from backend.datasources.service import get_source
    src = get_source(tenant_id, source_id)
    if not src:
        raise _reject("data_source_not_found", f"Data source {source_id} not found", 404,
                      source_id=source_id)
    if src.get("source_type") != "sql":
        raise _reject("inventory_sync_source_not_database",
                      "Inventory sync reads a connected database, not an uploaded file.")
    return src


def _text(value: Any, max_len: int = 256) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_len or "\x00" in text:
        raise _reject("inventory_sync_value_invalid", "A value is too long or not valid.",
                      max=max_len)
    return text


def _float_in(value: Any, lo: float, hi: float, name: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float("nan")
    if not (lo <= v <= hi):
        raise _reject("inventory_sync_value_out_of_range", f"{name} must be between {lo} and {hi}.",
                      field=name, min=lo, max=hi)
    return v


def _int_in(value: Any, lo: int, hi: int, name: str) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = lo - 1
    if not (lo <= v <= hi):
        raise _reject("inventory_sync_value_out_of_range", f"{name} must be between {lo} and {hi}.",
                      field=name, min=lo, max=hi)
    return v


def normalize(tenant_id: str, body: dict, existing: Optional[dict] = None) -> dict:
    """The stored form of a profile from a create/update body. On update,
    omitted keys keep their stored value."""
    base = dict(existing or {})
    for key in _EDITABLE:
        if key in body:
            base[key] = body[key]

    name = _text(base.get("name"), MAX_NAME)
    if not name:
        raise _reject("inventory_sync_name_required", "Give the sync a name.")
    kind = base.get("source_kind")
    source_id = _text(base.get("data_source_id"), 64)
    if kind == "push":
        # Rows arrive by POST (backend/inventory_sync/ingest.py): no database.
        source_id, engine = None, "push"
    else:
        if not source_id:
            raise _reject("inventory_sync_source_required", "Choose the database to read from.")
        src = _source(tenant_id, source_id)
        engine = (src.get("sql_config") or {}).get("engine") or "postgresql"
        kind = kind or ("collection" if engine == "mongodb" else "table")
    if kind not in SOURCE_KINDS:
        raise _reject("inventory_sync_source_kind_invalid", "Unknown source kind.", kind=str(kind)[:20])
    if engine == "push":
        pass
    elif engine == "mongodb" and kind not in MONGO_KINDS:
        raise _reject("inventory_sync_source_kind_invalid",
                      "A MongoDB source reads a collection or an aggregation pipeline.", kind=kind)
    elif engine != "mongodb" and kind not in SQL_KINDS:
        raise _reject("inventory_sync_source_kind_invalid",
                      "A SQL database reads a table, a view or a read-only query.", kind=kind)

    source_schema = _text(base.get("source_schema"))
    source_object = _text(base.get("source_object"))
    source_query = base.get("source_query")
    source_query = None if source_query in (None, "") else str(source_query)
    if source_query is not None and len(source_query) > 100_000:
        raise _reject("inventory_sync_value_invalid", "The query is too long.", max=100_000)

    if kind in ("table", "collection", "pipeline") and not source_object:
        raise _reject("inventory_sync_object_required", "Choose the table or collection to read.")
    if kind == "query":
        if not source_query or not source_query.strip():
            raise _reject("inventory_sync_query_required", "Write the read-only query to run.")
        from backend.datasources.sql_guard import validate_read_only_sql
        validate_read_only_sql(source_query, engine)
        source_object = None
    if kind in MONGO_KINDS:
        from backend.datasources import mongo
        if kind == "pipeline":
            mongo.validate_pipeline(_parse_json(source_query, list))
        elif source_query:
            mongo.validate_filter(_parse_json(source_query, dict))

    mapping = F.normalize_mapping(base.get("mapping") or {})
    filters = F.normalize_filters(base.get("filters"))

    mode = base.get("mode") or "full"
    if mode not in MODES:
        raise _reject("inventory_sync_mode_invalid", "Unknown sync mode.", mode=str(mode)[:20])
    watermark_column = _text(base.get("watermark_column"))
    if mode == "incremental" and not watermark_column:
        raise _reject("inventory_sync_watermark_required",
                      "An incremental sync needs the column that says when a row last changed.")

    missing_policy = base.get("missing_policy") or "ignore"
    negative_policy = base.get("negative_policy") or "reject"
    duplicate_policy = base.get("duplicate_policy") or "reject"
    for value, allowed, field in ((missing_policy, MISSING_POLICIES, "missing_policy"),
                                  (negative_policy, NEGATIVE_POLICIES, "negative_policy"),
                                  (duplicate_policy, DUPLICATE_POLICIES, "duplicate_policy")):
        if value not in allowed:
            raise _reject("inventory_sync_policy_invalid", f"Unknown {field}.", field=field,
                          value=str(value)[:30])
    if mode == "incremental" and missing_policy != "ignore":
        # An incremental read only sees rows that changed: "absent" means
        # "unchanged", never "gone". Zeroing on it would empty the catalogue.
        raise _reject("inventory_sync_policy_invalid",
                      "An incremental sync cannot tell a vanished SKU from an unchanged one.",
                      field="missing_policy", value=missing_policy)

    from backend.inventory_sync import strategies
    slug = base.get("slug")
    slug = None if slug in (None, "") else str(slug).strip().lower()
    if slug is not None and not _SLUG.match(slug):
        raise _reject("inventory_sync_slug_invalid",
                      "The key may use lowercase letters, digits, '-' and '_' (up to 63).", value=slug[:70])
    stock_model = base.get("stock_model") or "snapshot"
    if stock_model not in strategies.STOCK_MODELS:
        raise _reject("inventory_sync_model_invalid", "Unknown stock model.", reason="stock_model",
                      value=str(stock_model)[:20])
    model_config = base.get("model_config") or {}
    if not isinstance(model_config, dict):
        raise _reject("inventory_sync_model_invalid", "model_config must be an object.", reason="not_object")
    if stock_model == "aggregate":
        fn = str(model_config.get("aggregate") or "sum").lower()
        if fn not in strategies.AGGREGATES:
            raise _reject("inventory_sync_model_invalid", "Unknown aggregate.", reason="aggregate", value=fn[:20])
        if fn != "sum" and engine in ("mongodb", "push"):
            raise _reject("inventory_sync_model_invalid",
                          "MIN and MAX need a source that can group (SQL); this one is summed row by row.",
                          reason="aggregate_needs_sql", value=fn)
        model_config = {"aggregate": fn,
                        "group_by": [str(c)[:256] for c in (model_config.get("group_by") or [])][:10]}
    elif stock_model == "ledger":
        from backend.inventory_sync import ledger
        model_config = ledger.normalize_config(model_config, engine)
    else:
        model_config = {}
    if stock_model != "snapshot" and filters:
        # Row filters run after the source has grouped the rows, where their
        # columns no longer exist. Filter in the saved query / view, or with
        # the ledger's posted statuses.
        raise _reject("inventory_sync_model_invalid",
                      "Filters apply to snapshot profiles; filter in the query, or use posted statuses.",
                      reason="filters_need_snapshot")
    strategy = base.get("strategy") or "auto"
    if strategy not in strategies.STRATEGIES:
        raise _reject("inventory_sync_strategy_invalid", "Unknown change-capture strategy.",
                      value=str(strategy)[:30])
    if strategy in _ENGINE_STRATEGY and engine != _ENGINE_STRATEGY[strategy]:
        raise _reject("inventory_sync_strategy_invalid",
                      f"{strategy} exists only on {_ENGINE_STRATEGY[strategy]}.",
                      value=strategy, engine=engine)
    if strategy == "ledger_cursor" and stock_model != "ledger":
        raise _reject("inventory_sync_strategy_invalid", "ledger_cursor applies to ledger profiles.",
                      value=strategy)
    if strategy == "watermark" and not watermark_column:
        raise _reject("inventory_sync_watermark_required",
                      "The watermark strategy needs the column that says when a row last changed.")
    freshness = base.get("freshness") or "scheduled"
    if freshness not in FRESHNESS:
        raise _reject("inventory_sync_freshness_invalid", "Unknown freshness mode.", value=str(freshness)[:20])
    incremental_capable = (mode == "incremental" or stock_model == "ledger" or kind == "push"
                           or strategy in _ENGINE_STRATEGY)
    if freshness == "continuous" and not incremental_capable:
        # Re-reading a whole table every few seconds would load the customer's
        # database for nothing: continuous needs something that reads changes.
        raise _reject("inventory_sync_continuous_needs_incremental",
                      "Continuous mode needs a way to read only what changed (an updated-at column, "
                      "a movement ledger or a change-capture strategy).")

    return {
        "slug": slug, "priority": _int_in(base.get("priority", 100), 1, 10_000, "priority"),
        "stock_model": stock_model, "model_config": model_config, "strategy": strategy,
        "freshness": freshness,
        "poll_seconds": _int_in(base.get("poll_seconds", 60), min_poll(), 24 * 3600, "poll_seconds"),
        "reconcile_minutes": _int_in(base.get("reconcile_minutes", 360), 5, 7 * 24 * 60, "reconcile_minutes"),
        "brake_max_jump_pct": _float_in(base.get("brake_max_jump_pct", 20), 0, 100, "brake_max_jump_pct"),
        "name": name, "data_source_id": source_id, "source_kind": kind,
        "source_schema": source_schema, "source_object": source_object,
        "source_query": source_query, "mapping": mapping, "filters": filters,
        "default_warehouse": _text(base.get("default_warehouse"), F.MAX_TEXT),
        "mode": mode, "watermark_column": watermark_column,
        "overlap_minutes": _int_in(base.get("overlap_minutes", 10), 0, 24 * 60, "overlap_minutes"),
        "interval_minutes": _int_in(base.get("interval_minutes", 60), min_interval(),
                                    MAX_INTERVAL_MINUTES, "interval_minutes"),
        "missing_policy": missing_policy, "negative_policy": negative_policy,
        "duplicate_policy": duplicate_policy,
        "allow_new_warehouses": bool(base.get("allow_new_warehouses", False)),
        "brake_max_vanished_pct": _float_in(base.get("brake_max_vanished_pct", 20), 0, 100,
                                            "brake_max_vanished_pct"),
        "brake_min_row_ratio": _float_in(base.get("brake_min_row_ratio", 0.5), 0, 1,
                                         "brake_min_row_ratio"),
        "brake_max_rejected_pct": _float_in(base.get("brake_max_rejected_pct", 50), 0, 100,
                                            "brake_max_rejected_pct"),
        "engine": engine,
    }


def _values(cfg: dict) -> tuple:
    return tuple(_json(cfg[c]) if c in _JSON_COLUMNS else cfg[c] for c in _COLUMNS)


def _parse_json(text: Optional[str], kind: type) -> Any:
    try:
        value = json.loads(text or ("[]" if kind is list else "{}"))
    except (TypeError, ValueError):
        raise _reject("mongo_query_invalid", "The query is not valid JSON.", reason="not_json")
    if not isinstance(value, kind):
        raise _reject("mongo_query_invalid", "The query has the wrong shape.",
                      reason="not_array" if kind is list else "not_object")
    return value


# ── Reads ─────────────────────────────────────────────────────────────────────

def get(tenant_id: str, profile_id: str) -> Optional[dict]:
    return query_one("SELECT * FROM inventory_sync_profiles WHERE id = %s AND tenant_id = %s",
                     (profile_id, tenant_id))


def require(tenant_id: str, profile_id: str) -> dict:
    row = get(tenant_id, profile_id)
    if not row:
        raise _not_found(profile_id)
    return row


def list_for_tenant(tenant_id: str) -> list[dict]:
    return query("SELECT * FROM inventory_sync_profiles WHERE tenant_id = %s ORDER BY created_at",
                 (tenant_id,))


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def is_stale(row: dict, now: Optional[datetime] = None) -> bool:
    """Enabled, and no success within twice its interval (counted from its
    creation when it never succeeded)."""
    if not row.get("enabled"):
        return False
    now = now or datetime.now(timezone.utc)
    since = row.get("last_success_at") or row.get("created_at")
    if since is None:
        return False
    if row.get("freshness") == "on_demand":
        return False
    if row.get("freshness") == "continuous":
        return now - since > timedelta(seconds=max(2 * int(row.get("poll_seconds") or 60), 600))
    return now - since > timedelta(minutes=2 * int(row.get("interval_minutes") or 60))


def public(row: dict) -> dict:
    """What the API returns: everything but the lease internals, plus the
    status the screen needs (age of the last success, stale, brake)."""
    if not row:
        return row
    now = datetime.now(timezone.utc)
    out = {k: _iso(v) for k, v in row.items() if k not in ("lease_owner",)}
    last = row.get("last_success_at")
    out["last_success_age_s"] = int((now - last).total_seconds()) if last else None
    out["stale"] = is_stale(row, now)
    out["running"] = bool(row.get("lease_until") and row["lease_until"] > now)
    out["braked"] = row.get("last_status") == "braked"
    out["owned_fields"] = F.owned_fields(row.get("mapping") or {})
    out["needs_dry_run"] = row.get("dry_run_hash") != row.get("config_hash")
    return out


# ── Writes ────────────────────────────────────────────────────────────────────

def create(tenant_id: str, user_id: str, body: dict) -> dict:
    from backend.entitlements.service import limit_guard
    cfg = normalize(tenant_id, body)
    pid = generate_id("isp")
    with limit_guard(tenant_id) as conn:
        count = query_one("SELECT COUNT(*) AS n FROM inventory_sync_profiles WHERE tenant_id = %s",
                          (tenant_id,), conn=conn)["n"]
        if count >= max_profiles():
            raise _reject("inventory_sync_profile_limit",
                          f"This account already has {count} sync profiles, the most allowed.",
                          409, max=max_profiles())
        cols = ", ".join(_COLUMNS)
        phs = ", ".join(["%s"] * len(_COLUMNS))
        try:
            execute(
                f"INSERT INTO inventory_sync_profiles (id, tenant_id, {cols}, config_hash, created_by) "
                f"VALUES (%s, %s, {phs}, %s, %s)",
                (pid, tenant_id, *_values(cfg), config_hash(cfg), user_id), conn=conn)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "pgcode", None) == "23505":
                raise _reject("inventory_sync_slug_taken", "Another sync already uses this key.", 409,
                              slug=cfg.get("slug")) from None
            raise
    return require(tenant_id, pid)


def update(tenant_id: str, profile_id: str, body: dict) -> tuple[dict, bool]:
    """(updated row, whether the edit turned the profile off)."""
    existing = require(tenant_id, profile_id)
    cfg = normalize(tenant_id, body, existing)
    new_hash = config_hash(cfg)
    disable = bool(existing.get("enabled")) and new_hash != existing.get("config_hash")
    # A change of watermark column (or of source) makes the stored watermark
    # meaningless: start the next incremental read from the beginning.
    reset_watermark = (cfg["watermark_column"] != existing.get("watermark_column")
                       or cfg["data_source_id"] != existing.get("data_source_id")
                       or cfg["source_object"] != existing.get("source_object")
                       or cfg["source_query"] != existing.get("source_query"))
    sets = ", ".join(f"{c}=%s" for c in _COLUMNS)
    try:
        execute(
            f"UPDATE inventory_sync_profiles SET {sets}, config_hash=%s, "
            "enabled = CASE WHEN %s THEN FALSE ELSE enabled END, "
            "watermark_value = CASE WHEN %s THEN NULL ELSE watermark_value END, "
            "cursor_value = CASE WHEN %s THEN NULL ELSE cursor_value END, "
            "health = CASE WHEN %s AND health = 'paused' THEN 'new' ELSE health END, "
            "updated_at=NOW() WHERE id=%s AND tenant_id=%s",
            (*_values(cfg), new_hash, disable, reset_watermark, reset_watermark,
             new_hash != existing.get("config_hash"), profile_id, tenant_id))
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "pgcode", None) == "23505":
            raise _reject("inventory_sync_slug_taken", "Another sync already uses this key.", 409,
                          slug=cfg.get("slug")) from None
        raise
    return require(tenant_id, profile_id), disable


def delete(tenant_id: str, profile_id: str) -> dict:
    row = require(tenant_id, profile_id)
    if row.get("lease_until") and row["lease_until"] > datetime.now(timezone.utc):
        raise _reject("inventory_sync_running", "This sync is running; wait for it to finish.", 409)
    execute("DELETE FROM inventory_sync_profiles WHERE id = %s AND tenant_id = %s",
            (profile_id, tenant_id))
    return row


def set_enabled(tenant_id: str, profile_id: str, enabled: bool) -> dict:
    row = require(tenant_id, profile_id)
    if enabled:
        if row.get("dry_run_hash") != row.get("config_hash"):
            raise _reject("inventory_sync_dry_run_required",
                          "Preview this sync (dry run) before turning it on.", 409)
        if row.get("data_source_id"):
            src = _source(tenant_id, row["data_source_id"])
            if src.get("connection_status") != "connected":
                raise _reject("data_source_not_connected",
                              "This data source is not connected. Test the connection first.", 400)
        execute(
            "UPDATE inventory_sync_profiles SET enabled = TRUE, consecutive_failures = 0, "
            "next_run_at = COALESCE(next_run_at, NOW()), stale_alerted_at = NULL, updated_at = NOW() "
            "WHERE id = %s AND tenant_id = %s",
            (profile_id, tenant_id),
        )
    else:
        execute("UPDATE inventory_sync_profiles SET enabled = FALSE, updated_at = NOW() "
                "WHERE id = %s AND tenant_id = %s", (profile_id, tenant_id))
    return require(tenant_id, profile_id)


def mark_dry_run(tenant_id: str, profile_id: str, hash_value: str) -> None:
    execute("UPDATE inventory_sync_profiles SET dry_run_hash = %s, dry_run_at = NOW() "
            "WHERE id = %s AND tenant_id = %s", (hash_value, profile_id, tenant_id))


def list_runs(tenant_id: str, profile_id: str, limit: int = 50) -> list[dict]:
    require(tenant_id, profile_id)
    rows = query(
        "SELECT * FROM inventory_sync_runs WHERE tenant_id = %s AND profile_id = %s "
        "ORDER BY started_at DESC LIMIT %s", (tenant_id, profile_id, limit))
    return [{k: _iso(v) for k, v in r.items()} for r in rows]

"""
Tenant data export (ZIP) and cascade delete — Costa Rica Ley 8968 / GDPR-style
right to export and erasure.

Every query here is scoped by tenant_id (or, for the `tenants` row itself, by
id) so a request for one tenant can never surface another tenant's rows.
Secrets are never exported: `hashed_password`, refresh-token hashes,
password-change-code hashes, API-key hashes and webhook signing secrets are
either excluded via an explicit column list or the whole table is omitted
(see _OMITTED_FROM_EXPORT below).

FK cascade note (verified against backend/db/migrations.py): `tenants(id)` is
declared `ON DELETE CASCADE` on exactly four child tables — users, sessions,
datasets, jobs — plus whatever cascades transitively from those (e.g.
refresh_tokens/pw_change_codes/user_permissions via users; session_configs/
session_results/training_logs via sessions). Every OTHER tenant-scoped table
(inventory_stock, suppliers, inventory_po_log/items, documents, api_keys,
webhooks, chats, etc.) declares `tenant_id TEXT NOT NULL` with NO foreign key
at all, so a bare `DELETE FROM tenants` would silently orphan that data
instead of erasing it. `delete_tenant()` below therefore deletes every
tenant-scoped table explicitly, children-before-parents, inside one
transaction, and only then deletes the `tenants` row (which cascades
whatever the explicit list already emptied).
"""
import io
import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.db.connection import get_conn, query, query_one

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

# (file stem, table, SELECT column list). "*" means every column is safe to
# export; an explicit list is used wherever a column must be excluded.
_EXPORT_SPECS: list[tuple[str, str, str]] = [
    ("users", "users",
     "id, tenant_id, email, full_name, role, email_verified, status, "
     "last_login_at, pending_email, whatsapp_number, terms_accepted_at, "
     "terms_version, created_at, updated_at"),
    ("datasets", "datasets", "*"),
    ("sessions", "sessions", "*"),
    ("session_configs", "session_configs", "*"),
    ("session_results", "session_results", "*"),
    ("training_logs", "training_logs", "*"),
    ("jobs", "jobs", "*"),
    ("inventory_stock", "inventory_stock", "*"),
    ("inventory_snapshots", "inventory_snapshots", "*"),
    ("inventory_events", "inventory_events", "*"),
    ("inventory_event_multipliers", "inventory_event_multipliers", "*"),
    ("inventory_po_log", "inventory_po_log", "*"),
    ("inventory_po_items", "inventory_po_items", "*"),
    ("inventory_shrinkage", "inventory_shrinkage", "*"),
    ("stock_counts", "stock_counts", "*"),
    ("stock_count_lines", "stock_count_lines", "*"),
    ("stock_adjustments", "stock_adjustments", "*"),
    ("inventory_overstock_snapshots", "inventory_overstock_snapshots", "*"),
    ("inventory_roi_email_log", "inventory_roi_email_log", "*"),
    ("suppliers", "suppliers", "*"),
    ("sku_suppliers", "sku_suppliers", "*"),
    ("supplier_lead_time_obs", "supplier_lead_time_obs", "*"),
    ("supplier_price_breaks", "supplier_price_breaks", "*"),
    ("bom_items", "bom_items", "*"),
    ("warehouses", "warehouses", "*"),
    ("documents", "documents", "*"),
    ("chats", "chats", "*"),
    ("chat_messages", "chat_messages", "*"),
    ("accuracy_snapshots", "accuracy_snapshots", "*"),
    ("forecast_overrides", "forecast_overrides", "*"),
    ("forecast_adjustments", "forecast_adjustments", "*"),
    ("committed_demand", "committed_demand", "*"),
    ("spike_edits", "spike_edits", "*"),
    ("spike_edit_applications", "spike_edit_applications", "*"),
    ("sku_analogies", "sku_analogies", "*"),
    ("po_approval_rules", "po_approval_rules", "*"),
    ("po_approvals", "po_approvals", "*"),
    ("scheduled_jobs", "scheduled_jobs", "*"),
    ("schedule_runs", "schedule_runs", "*"),
    ("session_manifests", "session_manifests", "*"),
    ("session_accuracy_tracking", "session_accuracy_tracking", "*"),
    # The address token is a credential: only who may send, never the token.
    ("inbound_email_addresses", "inbound_email_addresses",
     "tenant_id, allowed_senders, created_at, rotated_at"),
    ("inbound_email_messages", "inbound_email_messages", "*"),
    # key_hash / secret are never exported — only metadata about the key/hook.
    ("api_keys", "api_keys", "id, tenant_id, name, last_used, created_at"),
    # Calls per key per day: what a call-based bill is computed from.
    ("api_usage_daily", "api_usage_daily", "*"),
    ("webhooks", "webhooks", "id, tenant_id, url, events, created_at"),
    ("user_permissions", "user_permissions", "*"),
    # Which sign-in providers each person linked. Who they are at Google /
    # Microsoft / Apple is the person's data, so it travels with the export.
    ("user_identities", "user_identities",
     "id, user_id, tenant_id, provider, subject, email, created_at, last_used_at"),
]

# Deliberately NOT exported: pure security/credential artifacts, not "the
# tenant's data" in the Ley 8968 / GDPR sense — refresh_tokens and
# pw_change_codes store only hashes anyway, and auth_rate_events is keyed by a
# generic rate-limit key (may mix identifiers across tenants), not owned rows.
_OMITTED_FROM_EXPORT = ("refresh_tokens", "pw_change_codes", "auth_rate_events")


def _json_default(obj: Any) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def _dump(rows: Any) -> bytes:
    return json.dumps(rows, default=_json_default, indent=2, ensure_ascii=False).encode("utf-8")


def build_export_zip(tenant_id: str) -> bytes:
    """
    Builds an in-memory ZIP with one JSON file per tenant-owned table plus a
    manifest.json. Returns the ZIP's raw bytes.
    """
    tenant = query_one("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
    if tenant is None:
        raise ValueError(f"Tenant not found: {tenant_id}")

    buf = io.BytesIO()
    manifest: dict[str, Any] = {
        "tenant_id": tenant_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tables": {},
        "omitted": list(_OMITTED_FROM_EXPORT),
    }

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("tenant.json", _dump(tenant))
        manifest["tables"]["tenant"] = 1

        for stem, table, cols in _EXPORT_SPECS:
            rows = query(f"SELECT {cols} FROM {table} WHERE tenant_id = %s", (tenant_id,))
            zf.writestr(f"{stem}.json", _dump(rows))
            manifest["tables"][stem] = len(rows)

        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Cascade delete
# ─────────────────────────────────────────────────────────────────────────────

# Tenant-scoped tables NOT covered by an FK cascade from `tenants`, deleted
# explicitly, children-before-parents so no live FK constraint is ever
# tripped (see module docstring for the verification against migrations.py).
_DELETE_ORDER: list[str] = [
    # Both added 2026-09-13. They DO carry `REFERENCES tenants(id) ON DELETE
    # CASCADE`, so their rows were already going with the tenant — the
    # behavioural test proves it. They are listed anyway because this list is
    # the reviewable answer to "what belongs to a tenant", and a table that is
    # only cleaned by a cascade is one `ON DELETE` clause away from being
    # forgotten for real. The guard that names them is doing its job.
    "service_config",
    "training_run_metrics",
    "upgrade_requests",
    "whatsapp_conversations",
    "chat_messages",
    "chats",
    "po_approvals",
    "po_approval_rules",
    "forecast_adjustments",
    "committed_demand",
    "spike_edit_applications",
    "spike_edits",
    "sku_analogies",
    "inventory_po_items",
    "supplier_lead_time_obs",
    "inventory_po_log",
    "sku_suppliers",
    "supplier_price_breaks",
    "suppliers",
    "inventory_event_multipliers",
    "inventory_events",
    "bom_items",
    "inventory_transfer_items",
    "inventory_transfer_log",
    "transfer_lanes",
    "warehouses",
    "stock_defaults",
    "inventory_stock",
    "inventory_status_snapshot",
    "inventory_status_snapshot_meta",
    "inventory_snapshots",
    "inventory_recommendation_log",
    "stock_count_ops",
    "stock_count_lines",
    "stock_counts",
    "stock_adjustments",
    "inventory_shrinkage",
    "inventory_overstock_snapshots",
    "inventory_roi_email_log",
    "accuracy_snapshots",
    "forecast_overrides",
    "scheduled_jobs",
    "schedule_runs",
    "session_manifests",
    "session_accuracy_tracking",
    "inbound_email_messages",
    "inbound_email_addresses",
    "webhooks",
    "api_usage_daily",
    "api_keys",
    "documents",
    "user_permissions",
    "user_identities",
    "refresh_tokens",
    "pw_change_codes",
    "training_logs",
    "session_results",
    "session_configs",
    "report_runs",
    "scenarios",
    "jobs",
    "sessions",
    "datasets",
    "direct_messages",
    "activity_logs",
    "user_preferences",
    "users",
    # The trigger ledger goes last: deleting any table above appends to it.
    "status_input_bumps",
]

# storage/<category>/<tenant_id>/... — see backend/storage/paths.py. Every
# category there roots a per-tenant directory the same way, so removal is a
# single rmtree per category instead of one helper per file type.
_STORAGE_CATEGORIES = (
    "tenants", "users", "sessions", "datasets", "jobs", "artifacts",
    "pos", "documents", "logs",
)


def _delete_storage_files(tenant_id: str) -> list[str]:
    """Best-effort removal of on-disk files under storage/**/{tenant_id}."""
    removed: list[str] = []
    base = Path(settings.storage_path)
    for category in _STORAGE_CATEGORIES:
        target = base / category / tenant_id
        if target.exists():
            try:
                shutil.rmtree(target)
                removed.append(str(target))
            except OSError as exc:
                log.warning("Could not remove storage dir %s: %s", target, exc)
    return removed


def delete_tenant(tenant_id: str) -> dict:
    """
    Cascade-deletes a tenant and ALL of its data in a single transaction, then
    best-effort removes its on-disk storage. Explicit per-table deletes are
    used instead of relying solely on FK cascade because most tenant-scoped
    tables have no FK to `tenants` at all (see module docstring).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            for table in _DELETE_ORDER:
                cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))

    removed_dirs = _delete_storage_files(tenant_id)
    return {"tenant_id": tenant_id, "removed_storage_dirs": removed_dirs}

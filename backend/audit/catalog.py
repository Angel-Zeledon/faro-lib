"""What the audit trail covers, in one place.

An audit entry answers: WHO did WHAT to WHICH object, WHEN, and what changed.
Two sources feed the one trail, normalised by `backend/audit/service.py`:

* **Catalogued routes** (this file). `AuditMiddleware` writes an
  `audit.<noun>.<verb>` row after a successful call to any route listed in
  `ROUTES`, naming the actor from the request itself. Doing it in a middleware
  rather than in each handler is the point: the next endpoint somebody adds to a
  listed router is covered by adding one line here, and an endpoint cannot be
  "forgotten" in the middle of a handler. A handler that knows more (the
  previous value, the id of the object it just created) adds it with
  `backend.audit.note(request, ...)`.
* **Legacy events** that already record who/what (`record_event` rows and the
  one `session.delete` row). They are NOT written a second time; `LEGACY`
  maps them onto the same shape for reading.

Never put a secret in a note. Service credentials, passwords and key material
are not summarised, only their existence and target.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

PREFIX = "audit."


@dataclass(frozen=True)
class AuditRoute:
    action: str                          # "dataset.deleted" (stored as "audit.dataset.deleted")
    target_type: str
    target_param: Optional[str] = None   # path parameter that names the object


def _r(action: str, target_type: str, param: Optional[str] = None) -> AuditRoute:
    return AuditRoute(action, target_type, param)


# (HTTP method, route template without /api/v1) -> what it means.
ROUTES: dict[tuple[str, str], AuditRoute] = {
    # sessions (a forecast). Deleting one is already recorded by `session.delete`.
    ("POST", "/sessions"):                          _r("session.created", "session"),
    ("PATCH", "/sessions/{session_id}"):            _r("session.updated", "session", "session_id"),
    ("POST", "/sessions/{session_id}/train"):       _r("session.training_started", "session", "session_id"),
    ("GET", "/sessions/{session_id}/export-config"): _r("export.session_config", "session", "session_id"),
    ("GET", "/sessions/{session_id}/artifacts/download/{artifact_path:path}"):
        _r("export.artifact", "session", "session_id"),
    ("POST", "/sessions/{session_id}/reports/generate"): _r("export.report", "session", "session_id"),
    # schedules
    ("POST", "/sessions/{session_id}/schedule"):    _r("schedule.saved", "schedule", "session_id"),
    ("DELETE", "/sessions/{session_id}/schedule"):  _r("schedule.deleted", "schedule", "session_id"),
    # datasets
    ("POST", "/datasets"):                          _r("dataset.created", "dataset"),
    ("POST", "/data-sources/file"):                 _r("dataset.created", "dataset"),
    ("POST", "/data-sources/sql"):                  _r("dataset.created", "dataset"),
    ("POST", "/data-sources/{source_id}/file"):     _r("dataset.replaced", "dataset", "source_id"),
    ("PATCH", "/data-sources/{source_id}"):         _r("dataset.updated", "dataset", "source_id"),
    ("PATCH", "/data-sources/{source_id}/query"):   _r("dataset.updated", "dataset", "source_id"),
    ("PATCH", "/data-sources/{source_id}/sql-config"): _r("dataset.updated", "dataset", "source_id"),
    ("POST", "/data-sources/{source_id}/materialize"): _r("dataset.materialized", "dataset", "source_id"),
    ("POST", "/data-sources/{source_id}/save-as-new"): _r("dataset.created", "dataset", "source_id"),
    ("POST", "/data-sources/{source_id}/export-query"): _r("export.query", "dataset", "source_id"),
    ("DELETE", "/data-sources/{source_id}"):        _r("dataset.deleted", "dataset", "source_id"),
    # users (invites, role changes and deactivation are recorded by their own events)
    ("PATCH", "/users/{user_id}/permissions"):      _r("user.permissions_changed", "user", "user_id"),
    # warehouses
    ("POST", "/inventory/warehouses"):              _r("warehouse.created", "warehouse"),
    ("PATCH", "/inventory/warehouses/{name}"):      _r("warehouse.updated", "warehouse", "name"),
    ("PUT", "/inventory/warehouses/lanes"):         _r("warehouse.lanes_changed", "warehouse"),
    ("DELETE", "/inventory/warehouses/lanes"):      _r("warehouse.lanes_changed", "warehouse"),
    # configuration
    ("PUT", "/planning"):                           _r("config.changed", "setting"),
    ("PATCH", "/tenant/timezone"):                  _r("config.changed", "setting"),
    ("PATCH", "/tenant/currency"):                  _r("config.changed", "setting"),
    ("PUT", "/inventory/signal-thresholds"):        _r("config.changed", "setting"),
    # purchase-order approval: who must approve what is company policy
    ("POST", "/inventory/po-approval/rules"):       _r("config.changed", "setting"),
    ("PATCH", "/inventory/po-approval/rules/{rule_id}"): _r("config.changed", "setting", "rule_id"),
    ("DELETE", "/inventory/po-approval/rules/{rule_id}"): _r("config.changed", "setting", "rule_id"),
    ("PUT", "/inventory/po-approval/approvers/{user_id}"): _r("user.permissions_changed", "user", "user_id"),
    ("DELETE", "/inventory/signal-thresholds"):     _r("config.changed", "setting"),
    ("POST", "/inventory/service-level-classes/apply"): _r("config.changed", "setting"),
    ("PUT", "/service-config/tenant/services/{service_key}"):    _r("config.changed", "setting", "service_key"),
    ("DELETE", "/service-config/tenant/services/{service_key}"): _r("config.changed", "setting", "service_key"),
    # sales received by e-mail: the webhook writes its own row (the actor is the
    # system, not a signed-in person); the two admin actions go through here.
    ("POST", "/inbound/email"):                     _r("inbound_email.received", "dataset"),
    ("POST", "/inbound-email/regenerate"):          _r("inbound_email.address_regenerated", "inbound_email"),
    ("PUT", "/inbound-email/senders"):              _r("inbound_email.senders_changed", "inbound_email"),
    # integrations a person wires up
    ("POST", "/webhooks"):                          _r("webhook.created", "webhook"),
    ("DELETE", "/webhooks/{webhook_id}"):           _r("webhook.deleted", "webhook", "webhook_id"),
    ("POST", "/documents"):                         _r("document.created", "document"),
    ("DELETE", "/documents/{doc_id}"):              _r("document.deleted", "document", "doc_id"),
    # a person telling us what happened (the text itself is never audited)
    ("POST", "/feedback"):                          _r("feedback.sent", "feedback"),
    # data leaving the product
    ("GET", "/tenant/export"):                      _r("export.tenant_data", "tenant"),
    ("GET", "/inventory/status/export-po"):         _r("export.purchase_orders", "purchase_order"),
}

# Rows that already carry who/what, mapped onto the audit shape for reading.
# legacy action -> (target_type, audit action name)
LEGACY: dict[str, tuple[str, str]] = {
    "session.delete":                  ("session", "session.deleted"),
    # Sessions are permanent: "delete" archives, and archiving, restoring and
    # running a back-test are each an act the trail must show.
    "session.archive":                 ("session", "session.archived"),
    "session.restore":                 ("session", "session.restored"),
    "session.backtest":                ("session", "session.backtest_started"),
    "training.completed":              ("session", "session.training_completed"),
    "training.failed":                 ("session", "session.training_failed"),
    "training.blocked":                ("session", "session.training_blocked"),
    "account.user_invited":            ("user", "user.invited"),
    "account.user_role_changed":       ("user", "user.role_changed"),
    "account.user_deactivated":        ("user", "user.deactivated"),
    "account.api_key_created":         ("api_key", "api_key.created"),
    "account.api_key_revoked":         ("api_key", "api_key.revoked"),
    "purchase.order_generated":        ("purchase_order", "purchase_order.created"),
    "purchase.order_sent":             ("purchase_order", "purchase_order.sent"),
    "purchase.order_not_sent":         ("purchase_order", "purchase_order.not_sent"),
    "purchase.reception_recorded":     ("purchase_order", "purchase_order.received"),
    "purchase.reception_undone":       ("purchase_order", "purchase_order.reception_undone"),
    "purchase.order_unsent":           ("purchase_order", "purchase_order.unsent"),
    "purchase.order_paid":             ("purchase_order", "purchase_order.paid"),
    "purchase.order_unpaid":           ("purchase_order", "purchase_order.unpaid"),
    "purchase.order_cancelled":        ("purchase_order", "purchase_order.cancelled"),
    "purchase.order_uncancelled":      ("purchase_order", "purchase_order.uncancelled"),
    "purchase.approval_requested":     ("purchase_order", "purchase_order.approval_requested"),
    "purchase.approval_approved":       ("purchase_order", "purchase_order.approval_approved"),
    "purchase.approval_rejected":       ("purchase_order", "purchase_order.approval_rejected"),
    "forecast.adjusted":               ("forecast_adjustment", "forecast_adjustment.created"),
    "forecast.spike_excluded":         ("spike_edit", "spike_edit.created"),
    "forecast.spike_restored":         ("spike_edit", "spike_edit.reverted"),
    "forecast.analogy_defined":        ("sku_analogy", "sku_analogy.created"),
    "forecast.analogy_reverted":       ("sku_analogy", "sku_analogy.reverted"),
    "committed_demand.created":        ("committed_demand", "committed_demand.created"),
    "committed_demand.imported":       ("committed_demand", "committed_demand.imported"),
    "committed_demand.changed":        ("committed_demand", "committed_demand.changed"),
    "data.stock_imported":             ("bulk_import", "bulk_import.stock"),
    "data.stock_import_partial":       ("bulk_import", "bulk_import.stock"),
    "data.suppliers_imported":         ("bulk_import", "bulk_import.suppliers"),
    "data.suppliers_import_partial":   ("bulk_import", "bulk_import.suppliers"),
    "data.orders_imported":            ("bulk_import", "bulk_import.purchase_orders"),
    "data.orders_import_partial":      ("bulk_import", "bulk_import.purchase_orders"),
    "data.transfer_created":           ("transfer", "transfer.created"),
    "data.shrinkage_recorded":         ("shrinkage", "shrinkage.recorded"),
    "data.stock_count_applied":        ("stock_count", "stock_count.applied"),
    "api_write":                       ("api_call", "api_call.write"),
}

# The target types the trail can be filtered by.
TARGET_TYPES = sorted({r.target_type for r in ROUTES.values()}
                      | {t for t, _ in LEGACY.values()})


def audit_action(action: str) -> str:
    return PREFIX + action


def actions_for_target_type(target_type: str) -> list[str]:
    """Every stored action name whose target is `target_type`."""
    found = [audit_action(r.action) for r in ROUTES.values() if r.target_type == target_type]
    found += [legacy for legacy, (t, _) in LEGACY.items() if t == target_type]
    return sorted(set(found))


def all_stored_actions() -> list[str]:
    return sorted({audit_action(r.action) for r in ROUTES.values()} | set(LEGACY))

"""Every read the assistant makes about an account, and nothing that writes.

One `AccountData` lives for one turn. Each accessor is lazy and cached, so the
context builder and the tools share a single read of the expensive things (the
semáforo is a full inventory computation) and a turn that never asks about
suppliers never reads them.

## Why each accessor calls the HTTP endpoint's own function

Same rule as `backend/mcp/catalog.py`: the assistant reads the account through
the exact functions FastAPI routes the screens' GET requests to — not a copy of
their bodies and not the service underneath. Three reasons:

  * One answer per question. "No session_id means the active session at the
    tenant's planning grain" lives in the endpoint. The WhatsApp bot used to
    read `get_latest_completed_session` with no period instead, so a weekly
    tenant's per-week demand was judged as daily and the bot could disagree
    with `/compras` about what is red.
  * Tenancy comes from the `CurrentUser`, exactly as for a browser request. No
    accessor takes a tenant id from the model.
  * The read-only guarantee is structural: `test_assistant.py` resolves every
    router function called here to its FastAPI route and demands `{GET}`.

The two service reads below (`get_tenant`, `get_preferences`) are plain
SELECTs with no route of their own that returns the same thing; the test pins
them by name.

Every accessor degrades to an empty value and records WHY in `self.missing`,
so the context can say "no hay análisis todavía" instead of an empty section
the model would read as "nothing to worry about".
"""
from __future__ import annotations

import logging
from functools import cached_property
from typing import Any

from backend.auth.guards import CurrentUser

log = logging.getLogger(__name__)


def _data(envelope: Any) -> Any:
    """`ok(...)` wraps every endpoint answer in {"data": ...}."""
    if isinstance(envelope, dict) and "data" in envelope:
        return envelope["data"]
    return envelope


class AccountData:
    """Lazy, cached, read-only view of one tenant for one user and one turn."""

    def __init__(self, user: CurrentUser):
        self.user = user
        # section key -> machine reason ("no_completed_session", "error", ...)
        self.missing: dict[str, str] = {}

    def _safe(self, key: str, fn, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — degrade per section, never the turn
            code = getattr(exc, "code", None) or type(exc).__name__
            self.missing[key] = str(code)
            log.info("[assistant] %s unavailable for tenant=%s: %s",
                     key, self.user.tenant_id, exc)
            return default

    # ── Who is asking ────────────────────────────────────────────────────────

    @cached_property
    def me(self) -> dict:
        from backend.api.v1 import users as users_router
        return self._safe("me", lambda: _data(users_router.get_me(user=self.user)), {}) or {}

    @cached_property
    def tenant(self) -> dict:
        # Service read (plain SELECT): no GET route returns the company name.
        from backend.tenants.service import get_tenant
        return self._safe("tenant", lambda: get_tenant(self.user.tenant_id), {}) or {}

    @cached_property
    def preferences(self) -> dict:
        # Service read (plain SELECT) — the same one GET /me/preferences makes.
        from backend.preferences.service import get_preferences
        return self._safe(
            "preferences",
            lambda: get_preferences(self.user.tenant_id, self.user.user_id), {},
        ) or {}

    @cached_property
    def currency(self) -> dict:
        from backend.api.v1 import currency as currency_router
        data = self._safe("currency", lambda: _data(currency_router.get_currency(user=self.user)), {})
        return (data or {}).get("current") or {}

    # ── The forecast and its freshness ───────────────────────────────────────

    @cached_property
    def planning(self) -> dict:
        from backend.api.v1 import planning as planning_router
        return self._safe("planning", lambda: _data(planning_router.get_planning(user=self.user)), {}) or {}

    @cached_property
    def freshness(self) -> dict:
        from backend.api.v1 import freshness as freshness_router
        return self._safe(
            "freshness", lambda: _data(freshness_router.get_data_freshness(user=self.user)), {},
        ) or {}

    # ── The semáforo ─────────────────────────────────────────────────────────

    @cached_property
    def briefing(self) -> dict:
        """What `/compras` shows: top risks, overstock, KPIs, demand changes."""
        from backend.api.v1 import inventory as inventory_router
        return self._safe(
            "briefing",
            lambda: _data(inventory_router.morning_briefing(
                session_id=None, service_level=0.95, user=self.user)),
            {},
        ) or {}

    @cached_property
    def status(self) -> dict:
        """Every SKU's row, as `/inventario` shows it. Loaded only when a
        question or a tool needs a product outside the briefing's top lists."""
        from backend.api.v1 import inventory as inventory_router
        return self._safe(
            "status",
            lambda: _data(inventory_router.inventory_status(
                session_id=None, service_level=0.95, signal=None, supplier=None,
                by_warehouse=False, limit=None, offset=0, sort="urgency", order=None, q=None,
                skus=None, user=self.user)),
            {},
        ) or {}

    @property
    def status_items(self) -> list[dict]:
        return list(self.status.get("items") or [])

    @cached_property
    def stock_rows(self) -> list[dict]:
        """The product catalogue as stored (cheap: no forecast). Used to find a
        product by name before paying for the full semáforo."""
        from backend.api.v1 import inventory as inventory_router
        return self._safe("stock", lambda: _data(inventory_router.list_stock(user=self.user)), []) or []

    def sku_suppliers(self, sku: str) -> list[dict]:
        from backend.api.v1 import inventory as inventory_router
        return self._safe(
            f"sku_suppliers:{sku}",
            lambda: _data(inventory_router.get_sku_suppliers(sku=sku, user=self.user)), [],
        ) or []

    def forecast(self, sku: str) -> dict:
        from backend.api.v1 import forecasts as forecasts_router
        session_id = self.planning.get("active_session_id")
        if not session_id:
            self.missing[f"forecast:{sku}"] = "no_completed_session"
            return {}
        return self._safe(
            f"forecast:{sku}",
            lambda: _data(forecasts_router.get_sku_intelligence(
                session_id=session_id, sku=sku, model=None, granularity=None,
                agg="sum", user=self.user)),
            {},
        ) or {}

    @cached_property
    def committed_demand(self) -> dict:
        """Open customer commitments with their at-risk verdict and the
        by-customer roll-up: what the commitments screen shows."""
        from backend.api.v1 import committed_demand as committed_router
        return self._safe(
            "committed_demand",
            lambda: _data(committed_router.list_commitments(
                sku=None, status="open", limit=500, user=self.user)),
            {},
        ) or {}

    # ── Purchase orders and suppliers ────────────────────────────────────────

    @cached_property
    def po_history(self) -> list[dict]:
        from backend.api.v1 import inventory as inventory_router
        return self._safe(
            "po_history", lambda: _data(inventory_router.po_history(limit=50, user=self.user)), [],
        ) or []

    @cached_property
    def overdue_pos(self) -> list[dict]:
        from backend.api.v1 import inventory as inventory_router
        return self._safe("overdue", lambda: _data(inventory_router.po_overdue(user=self.user)), []) or []

    def po_items(self, po_log_id: str) -> dict:
        from backend.api.v1 import inventory as inventory_router
        return self._safe(
            f"po_items:{po_log_id}",
            lambda: _data(inventory_router.po_items(po_log_id=po_log_id, user=self.user)), {},
        ) or {}

    @cached_property
    def suppliers(self) -> list[dict]:
        from backend.api.v1 import inventory as inventory_router
        return self._safe("suppliers", lambda: _data(inventory_router.list_suppliers(user=self.user)), []) or []

    @cached_property
    def scorecard(self) -> list[dict]:
        from backend.api.v1 import inventory as inventory_router
        return self._safe(
            "scorecard", lambda: _data(inventory_router.supplier_scorecard(user=self.user)), [],
        ) or []

    @cached_property
    def lead_time_alerts(self) -> list[dict]:
        from backend.api.v1 import inventory as inventory_router
        return self._safe(
            "lead_time_alerts",
            lambda: _data(inventory_router.supplier_lead_time_alerts(user=self.user)), [],
        ) or []

    # ── What this user did lately ────────────────────────────────────────────

    def activity(self, limit: int = 8) -> list[dict]:
        from backend.api.v1 import activity as activity_router
        data = self._safe(
            "activity",
            lambda: _data(activity_router.get_activity(
                user=self.user, limit=limit, offset=0, action=None)),
            {},
        ) or {}
        return list(data.get("items") or [])

    # ── Derived conveniences (no I/O of their own) ───────────────────────────

    @property
    def first_name(self) -> str:
        full = (self.me.get("full_name") or "").strip()
        if full:
            return full.split()[0]
        email = (self.me.get("email") or "").strip()
        return email.split("@")[0] if email else ""

    @property
    def company_name(self) -> str:
        return (self.tenant.get("name") or "").strip()

    @property
    def role(self) -> str:
        return self.me.get("role") or self.user.role

    @property
    def coverage_unit(self) -> str:
        return self.briefing.get("coverage_unit") or self.status.get("coverage_unit") or "day"

    def money(self, amount) -> str:
        from backend.formatting import money
        try:
            return money(float(amount), currency=self.currency or None)
        except (TypeError, ValueError):
            return "-"

"""The customer portal lives in Rust (backend-rs/src/routes/customer_portal.rs),
so the Python registries that watch FastAPI routes cannot see it. These checks
read the Rust source and the shared registries instead, so a one-sided edit
turns red:

* the event / audit vocabulary Rust writes is the one Python declares;
* the only unauthenticated routes are the two token routes, and every other
  handler goes through the person guard (never an API key);
* the Caddy example sends the whole group to Rust, with no Python failover;
* the page copy and error codes exist in both languages;
* the tenant-erasure and export lists know the three tables, and no export
  carries a credential;
* the public whitelist carries none of the fields a customer must never see.

No database is needed.
"""

import re
from pathlib import Path

import pytest

from backend.activity.events import EVENTS, REASONS
from backend.audit.catalog import LEGACY
from backend.inventory.customer_portal_migrations import MIGRATIONS
from backend.tenants import data_export

# Pure source and registry reads: they must run (and fail) with no database.
pytestmark = pytest.mark.offline

ROOT = Path(__file__).resolve().parents[2]
RUST = ROOT / "backend-rs" / "src"
PORTAL = (RUST / "routes" / "customer_portal.rs").read_text(encoding="utf-8")
TRANSLATIONS = (ROOT / "Frontend" / "src" / "i18n" / "translations.ts").read_text(encoding="utf-8")
CADDY = (ROOT / "deploy" / "rust-api" / "routes.d"
         / "46-customer-portal.caddy.example").read_text(encoding="utf-8")

PORTAL_EVENTS = sorted(a for a in EVENTS if a.startswith("customer_portal."))
SEVERITY = {"INFO": "info", "WARNING": "warning", "CRITICAL": "critical"}


def _keys(raw: str) -> tuple[str, ...]:
    return tuple(re.findall(r'"([a-z_]+)"', raw))


def test_there_are_seven_portal_events():
    assert len(PORTAL_EVENTS) == 7


def test_rust_activity_specs_equal_python_events():
    source = (RUST / "activity.rs").read_text(encoding="utf-8")
    found = {}
    for m in re.finditer(
        r'\(\s*"(customer_portal\.[a-z_]+)",\s*"(\w+)",\s*"(\w+)",\s*&\[(.*?)\],?\s*\)', source, re.DOTALL
    ):
        found[m.group(1)] = (m.group(2), m.group(3), _keys(m.group(4)))
    want = {a: (EVENTS[a].kind, EVENTS[a].severity, tuple(EVENTS[a].detail_keys)) for a in PORTAL_EVENTS}
    assert found == want


def test_rust_alert_feed_mirror_equals_python_events():
    source = (RUST / "routes" / "r1" / "alerts.rs").read_text(encoding="utf-8")
    found = {}
    for m in re.finditer(r'\("(customer_portal\.[a-z_]+)", "(\w+)", (\w+), &\[(.*?)\]\)', source):
        found[m.group(1)] = (m.group(2), SEVERITY[m.group(3)], _keys(m.group(4)))
    want = {a: (EVENTS[a].kind, EVENTS[a].severity, tuple(EVENTS[a].detail_keys)) for a in PORTAL_EVENTS}
    assert found == want


def test_rust_audit_legacy_equals_python():
    source = (RUST / "audit" / "catalog.rs").read_text(encoding="utf-8")
    found = {m.group(1): (m.group(2), m.group(3)) for m in re.finditer(
        r'\("(customer_portal\.[a-z_]+)", "(\w+)", "([\w.]+)"\)', source)}
    want = {a: LEGACY[a] for a in LEGACY if a.startswith("customer_portal.")}
    assert found == want and len(want) == 7


def test_the_warning_event_names_a_declared_reason():
    assert EVENTS["customer_portal.date_objected"].severity == "warning"
    assert "customer_date_objection" in REASONS
    assert '"customer_date_objection"' in PORTAL  # the Rust call site passes it


def _handlers() -> dict[str, str]:
    parts = re.split(r"\n(?:pub )?async fn (\w+)", PORTAL)
    # parts = [prefix, name1, body1, name2, body2, ...]; a body ends at the next fn
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


TENANT_HANDLERS = {"customers", "list_links", "create_link", "get_link", "update_link", "revoke_link",
                   "reopen_link", "set_promised_date"}
PUBLIC_HANDLERS = {"public_view", "public_respond"}


def test_only_the_two_token_routes_are_unauthenticated():
    routes = re.findall(r'\.route\("([^"]+)"', PORTAL)
    assert len(routes) == 8, "router() not found or a route was added without updating this test"
    public = [path for path in routes if "/public/" in path]
    assert sorted(public) == [
        "/api/v1/customer-portal/public/{token}",
        "/api/v1/customer-portal/public/{token}/respond",
    ]
    router = PORTAL[PORTAL.index("pub fn router()"):PORTAL.index("#[cfg(test)]")]
    registered = set(re.findall(r"(?:get|post|put|patch)\((\w+)\)", router))
    assert registered == TENANT_HANDLERS | PUBLIC_HANDLERS, registered
    handlers = _handlers()
    for name in PUBLIC_HANDLERS | {"public_view_inner", "public_respond_inner"}:
        body = handlers[name]
        assert "manager(" not in body and "current_user" not in body, name
    for name in TENANT_HANDLERS:
        assert "manager(" in handlers[name], f"{name} must authenticate through manager()"
    # Management is never open to API keys: the single route declaration says so.
    assert "Exposure::Internal(" in PORTAL and "Exposure::Exposed" not in PORTAL


def test_every_write_handler_asks_for_analyst_or_above():
    for name in ("create_link", "update_link", "revoke_link", "reopen_link", "set_promised_date"):
        assert "manager(&state, &actors, &headers, true)" in _handlers()[name], name
    for name in ("customers", "list_links", "get_link"):
        assert "manager(&state, &actors, &headers, false)" in _handlers()[name], name


def test_the_token_is_never_stored_or_logged():
    assert "token_hash" in PORTAL and "hash_token(&token)" in PORTAL
    insert = PORTAL[PORTAL.index("INSERT INTO customer_portal_links"):][:700]
    assert "token_hash" in insert and ", token," not in insert
    # nothing logs the presented token
    for line in PORTAL.splitlines():
        if "tracing::" in line:
            assert "token" not in line.replace("token_hash", ""), line


def test_caddy_example_sends_the_group_to_rust_without_python_failover():
    assert "path /api/v1/customer-portal/*" in CADDY or "customer-portal" in CADDY
    assert "api-rs:8021" in CADDY
    live = "\n".join(l for l in CADDY.splitlines() if not l.lstrip().startswith("#"))
    assert "api:8010" not in live, "new Rust routes have no Python twin to fail over to"


def _quote(key: str) -> str:
    return f"'{key}'"


def test_copy_exists_in_both_languages():
    def count(key: str) -> int:
        return len(re.findall(rf"^\s*'{re.escape(key)}':", TRANSLATIONS, re.MULTILINE))

    keys = [f"events.action.{a}" for a in PORTAL_EVENTS]
    keys += [f"audit.action.{LEGACY[a][1]}" for a in PORTAL_EVENTS]
    keys += ["audit.target.customer_portal_link", "events.reason.customer_date_objection"]
    codes = set(re.findall(r'"(customer_portal_[a-z_]+)"', PORTAL)) | {"too_many_attempts"}
    keys += [f"errors.{c}" for c in sorted(codes)]
    for key in keys:
        assert count(key) == 2, f"{key}: expected one es and one en entry, found {count(key)}"


def test_every_error_code_in_rust_is_translated_with_its_params():
    for code, params in (("customer_portal_body_too_large", "{max_kb}"),
                         ("customer_portal_link_limit", "{max}")):
        for m in re.finditer(rf"'errors\.{code}':\s*'([^']*)'", TRANSLATIONS):
            assert params in m.group(1)


def test_migrations_never_hold_a_plaintext_token():
    ddl = " ".join(sql for _, sql in MIGRATIONS)
    assert "token_hash      TEXT NOT NULL UNIQUE" in ddl
    assert not re.search(r"\btoken\s+TEXT", ddl)
    names = [n for n, _ in MIGRATIONS]
    assert len(names) == len(set(names))
    for table in ("customer_portal_links", "customer_portal_promised_dates", "customer_portal_events"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
        assert "ON DELETE CASCADE" in ddl


def test_erasure_and_export_know_the_portal_tables():
    tables = ("customer_portal_events", "customer_portal_promised_dates", "customer_portal_links")
    for t in tables:
        assert t in data_export._DELETE_ORDER
    order = data_export._DELETE_ORDER
    assert order.index("customer_portal_events") < order.index("customer_portal_links")
    assert order.index("customer_portal_promised_dates") < order.index("committed_demand")
    specs = {name: cols for name, _table, cols in data_export._EXPORT_SPECS}
    for t in tables:
        assert t in specs
    assert "token_hash" not in specs["customer_portal_links"]
    assert "ip_hash" not in specs["customer_portal_events"]


def test_public_whitelist_has_no_internal_field():
    page = re.search(r"PUBLIC_PAGE_KEYS: \[&str; \d+\] =\s*\[(.*?)\];", PORTAL, re.DOTALL)
    item = re.search(r"PUBLIC_COMMITMENT_KEYS: \[&str; \d+\] =\s*\[(.*?)\];", PORTAL, re.DOTALL)
    shown = set(_keys(page.group(1))) | set(_keys(item.group(1)))
    banned = {"stock", "current_stock", "min_stock", "unit_cost", "cost", "probability", "note", "warehouse",
              "warehouse_id", "supplier", "customer_key", "token", "token_hash", "created_by", "verdict",
              "on_top_of_base", "contract_id", "source", "risk", "at_risk", "shortfall", "other_customers"}
    assert not shown & banned
    assert shown == {"company", "customer", "language", "share_dates", "expires_at", "commitments", "id", "sku",
                     "description", "quantity", "requested_date", "status", "promised_date", "my_response"}
    # the stock table is read for one column only
    assert "s.display_name" in PORTAL and "s.current_stock" not in PORTAL and "s.unit_cost" not in PORTAL

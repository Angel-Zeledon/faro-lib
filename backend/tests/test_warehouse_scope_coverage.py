"""Every router function that touches warehouse-dimensioned data must say what a
warehouse-scoped user gets - or be on a written allow-list with the reason.

Same idea as `test_sessions_permanent.py`'s source scan: the endpoints exist
today and are covered by `test_warehouse_scope.py`, but the next endpoint is
written by somebody who has never heard of scopes, and a scope that holds for
the endpoints that existed on the day it was added is a scope that quietly stops
holding. This test makes that person's first run red, with the function's name.

How a function is judged "warehouse-dimensioned" (a scan cannot be perfect, so
it errs toward flagging): its source - signature and body - mentions a warehouse
or one of the tables/services that hold per-warehouse rows (see `_DIMENSIONED`).
A flagged function passes when its source calls the scope helper
(`wscope.` / `warehouse_scope`), which is how every guarded endpoint in the
repository says so - including the `Depends(wscope.po_guard)` form.

Everything else must be in `ALLOWED`, with a reason a reviewer can disagree with.
The scan has the limits of any text scan: it proves the helper is CALLED, not
that it is called on the right value - that is `test_warehouse_scope.py`'s job -
and a function that reaches warehouse data only through a helper whose name
matches none of the patterns is not flagged. The patterns are deliberately wide.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "api" / "v1"

_DIMENSIONED = re.compile(
    r"warehouse|bodega|inventory_stock|list_stock|get_stock|get_inventory_status"
    r"|morning_briefing|inventory_snapshot|stock_history|get_incoming|po_log|po_items"
    r"|reception|transfer|shrinkage|dead_capital|get_roi|freshness"
    # Customer commitments name a warehouse and carry a stock-based verdict.
    # `GET /committed-demand` slipped through for a whole release because its
    # body (`svc.list_for_tenant(...)`, `svc.annotate_risk(...)`) contained
    # none of the words above.
    r"|committed_demand|commitment|annotate_risk",
    re.I,
)

# Routers whose EVERY route is warehouse-dimensioned, whatever words its body
# happens to use: the per-function text scan missed the commitments list once,
# so a new route in one of these modules is flagged by where it lives.
DIMENSIONED_MODULES = {"committed_demand.py"}
_HELPER = re.compile(r"wscope\.|warehouse_scope|_require_transfer_end_in_scope")
_VERBS = {"get", "post", "put", "patch", "delete"}

# "module.py:function" -> why a warehouse-scoped user needs no special handling.
ALLOWED: dict[str, str] = {
    "inventory.py:list_shrinkage_reasons":
        "A static list of reason codes; no tenant data.",
    "inventory.py:download_po_pdf":
        "Served by capability URL (an unguessable order id) to a supplier's WhatsApp "
        "fetch, with no caller identity at all - there is no user to scope. The PDF "
        "is what was already sent to that supplier.",
    "inventory_import.py:po_import_preview":
        "A dry run over the caller's own uploaded file; writes nothing and reads no "
        "stored stock. The import itself (po_import) is guarded.",
    "webhooks.py:list_event_types":
        "A static catalogue of event names and payload keys; no tenant data.",
    "inventory.py:supplier_scorecard":
        "Supplier-level statistics (lead time, on-time and fill rate). Not keyed by "
        "warehouse and shows no stock or warehouse names.",
}


def _router_functions():
    for path in sorted(API_DIR.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_route = any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in _VERBS
                and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
                for d in node.decorator_list
            )
            if is_route:
                yield path.name, node.name, ast.get_source_segment(src, node) or ""


def _flagged():
    return [(f, n, seg) for f, n, seg in _router_functions()
            if f in DIMENSIONED_MODULES or _DIMENSIONED.search(seg)]


def test_the_scan_sees_the_routers():
    names = {f"{f}:{n}" for f, n, _ in _router_functions()}
    assert "inventory.py:list_stock" in names
    assert "inventory.py:receive_transfer" in names
    assert "po_payments.py:mark_po_paid" in names
    assert len(names) > 150, "the scan stopped finding routes - fix the scan, not the test"


def test_every_warehouse_dimensioned_endpoint_calls_the_scope_helper_or_is_allowed():
    missing = []
    for f, n, seg in _flagged():
        key = f"{f}:{n}"
        if _HELPER.search(seg) or key in ALLOWED:
            continue
        missing.append(key)
    assert not missing, (
        "These router functions touch warehouse-dimensioned data and neither call "
        "backend.auth.warehouse_scope (as `wscope`) nor appear in ALLOWED with a "
        "reason. Decide what a user limited to some warehouses gets: filter "
        "(`filter_rows`), refuse a write outside the scope (`require_in_scope`), or "
        "refuse company totals (`require_company_wide`):\n  " + "\n  ".join(sorted(missing))
    )


def test_the_allow_list_has_no_stale_entries():
    """An entry whose function is gone, or no longer flagged, or now guarded, is
    a lie waiting to hide the next unguarded endpoint of the same name."""
    flagged = {f"{f}:{n}": seg for f, n, seg in _flagged()}
    stale = []
    for key in ALLOWED:
        if key not in flagged:
            stale.append(f"{key} (not a flagged route any more)")
        elif _HELPER.search(flagged[key]):
            stale.append(f"{key} (it calls the helper now; drop the entry)")
    assert not stale, "Remove from ALLOWED:\n  " + "\n  ".join(stale)


def test_every_allow_list_entry_says_why():
    assert all(len(reason.strip()) > 15 for reason in ALLOWED.values())


def test_every_committed_demand_route_is_flagged_and_guarded():
    """The router that leaked: all of its routes are in the scan, and each one
    calls the scope helper."""
    routes = {n: seg for f, n, seg in _flagged() if f == "committed_demand.py"}
    assert {"list_commitments", "create_commitment", "create_commitments_bulk",
            "update_commitment", "set_commitment_status"} <= set(routes)
    unguarded = [n for n, seg in routes.items() if not _HELPER.search(seg)]
    assert not unguarded, unguarded


def test_a_new_route_in_a_dimensioned_module_is_caught_whatever_it_says(tmp_path, monkeypatch):
    """A route whose body names no warehouse word is still flagged when it lives
    in a module listed in DIMENSIONED_MODULES — the hole the commitments list
    fell through."""
    import backend.tests.test_warehouse_scope_coverage as me

    (tmp_path / "committed_demand.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/committed-demand/export')\n"
        "def export_everything(user=None):\n"
        "    return svc.list_for_tenant(user.tenant_id)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(me, "API_DIR", tmp_path)
    flagged = {f"{f}:{n}" for f, n, seg in me._flagged()
               if not me._HELPER.search(seg) and f"{f}:{n}" not in me.ALLOWED}
    assert flagged == {"committed_demand.py:export_everything"}


def test_the_scan_goes_red_for_an_unguarded_function(tmp_path, monkeypatch):
    """Break it deliberately: a new warehouse endpoint with no helper is caught."""
    import backend.tests.test_warehouse_scope_coverage as me

    (tmp_path / "newrouter.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/x')\n"
        "def list_bin_locations(warehouse: str, user=None):\n"
        "    return query('SELECT * FROM inventory_stock WHERE warehouse = %s', (warehouse,))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(me, "API_DIR", tmp_path)
    flagged = {f"{f}:{n}" for f, n, seg in me._flagged()
               if not me._HELPER.search(seg) and f"{f}:{n}" not in me.ALLOWED}
    assert flagged == {"newrouter.py:list_bin_locations"}

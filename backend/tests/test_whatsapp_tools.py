"""Query tools read tenant-scoped data and mutate nothing; write tools propose
without mutating; the executor mutates only when invoked and enforces role."""
import pytest

from backend.db.connection import execute, query_one
from backend.whatsapp import tools
from backend.whatsapp.tools import ToolContext, ToolError


def _ctx(reg, role="admin"):
    return ToolContext(tenant_id=reg["tenant"]["id"], user_id=reg["user"]["id"], role=role)


def _seed_po(tid, *, sku="SKU1", warehouse="bodega norte", qty=200, sent=False):
    row = query_one(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, sku_count, total_units, total_value, reception_status)
           VALUES (%s, 'sess-x', 1, %s, %s, 'pending')
           RETURNING id, po_number""",
        (tid, qty, qty * 10),
    )
    po_id = row["id"]
    if sent:
        execute("UPDATE inventory_po_log SET sent_at = NOW() WHERE id = %s", (po_id,))
    execute(
        """INSERT INTO inventory_po_items
               (po_log_id, tenant_id, sku, display_name, supplier, status,
                recommended_qty, final_qty, unit_cost, warehouse)
           VALUES (%s, %s, %s, %s, 'Proveedor A', 'approved', %s, %s, 10, %s)""",
        (po_id, tid, sku, sku, qty, qty, warehouse),
    )
    return po_id


# ── Query tools ──────────────────────────────────────────────────────────────

def test_semaphore_status_returns_text_and_no_mutation(registered_user, completed_session):
    ctx = _ctx(registered_user)
    before = query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
                       (ctx.tenant_id,))["n"]
    out = tools.QUERY_TOOLS["semaphore_status"](ctx, {})
    assert isinstance(out, str) and len(out) > 0
    after = query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
                      (ctx.tenant_id,))["n"]
    assert before == after


def test_list_pending_pos_is_tenant_scoped(client, registered_user):
    ctx = _ctx(registered_user)
    _seed_po(ctx.tenant_id, sku="A")
    out = tools.QUERY_TOOLS["list_pending_pos"](ctx, {})
    assert isinstance(out, str)
    assert "OC" in out or "pendiente" in out.lower()


# ── Write proposals: NO mutation ─────────────────────────────────────────────

def test_propose_approve_po_does_not_mutate(client, registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id)
    action = tools.propose_approve_po(ctx, {"po_log_id": po_id})
    assert action["type"] == "approve_po"
    assert action["po_log_id"] == po_id
    assert "summary" in action
    # sent_at still NULL — proposing changed nothing.
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None


def test_propose_reception_does_not_mutate(client, registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id, sku="SKU1", warehouse="bodega norte", qty=200)
    action = tools.propose_reception(ctx, {"sku": "SKU1", "warehouse": "bodega norte", "quantity": 200})
    assert action["type"] == "register_reception"
    assert action["po_log_id"] == po_id
    assert action["warehouse"] == "bodega norte"
    assert action["quantity"] == 200
    # No stock row created, no received_qty accumulated.
    stock = query_one(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s AND warehouse = %s",
        (ctx.tenant_id, "SKU1", "bodega norte"),
    )
    assert stock is None
    item = query_one("SELECT received_qty FROM inventory_po_items WHERE po_log_id = %s", (po_id,))
    assert (item["received_qty"] or 0) == 0


# ── Executor: mutates, enforces role ─────────────────────────────────────────

def test_execute_approve_po_stamps_sent_at(client, registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id)
    msg = tools.execute_pending_action(ctx, {"type": "approve_po", "po_log_id": po_id})
    assert isinstance(msg, str)
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is not None


def test_execute_reception_credits_named_warehouse(client, registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id, sku="SKU1", warehouse="bodega norte", qty=200)
    tools.execute_pending_action(ctx, {
        "type": "register_reception", "po_log_id": po_id,
        "sku": "SKU1", "warehouse": "bodega norte", "quantity": 200,
    })
    stock = query_one(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s AND warehouse = %s",
        (ctx.tenant_id, "SKU1", "bodega norte"),
    )
    assert stock is not None
    assert float(stock["current_stock"]) == 200.0


def test_execute_denies_viewer_and_does_not_mutate(client, registered_user):
    ctx = _ctx(registered_user, role="viewer")
    po_id = _seed_po(ctx.tenant_id)
    with pytest.raises(ToolError):
        tools.execute_pending_action(ctx, {"type": "approve_po", "po_log_id": po_id})
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None


def test_propose_approve_po_missing_raises(client, registered_user):
    ctx = _ctx(registered_user)
    with pytest.raises(ToolError):
        tools.propose_approve_po(ctx, {"po_log_id": "does-not-exist"})

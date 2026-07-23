"""Agent: routing + the two-step confirmation gate. LLM is mocked."""
import json
from unittest import mock

from backend.db.connection import query_one, execute
from backend.whatsapp import agent
from backend.whatsapp.tools import ToolContext


def _ctx(reg, role="admin"):
    return ToolContext(tenant_id=reg["tenant"]["id"], user_id=reg["user"]["id"], role=role)


def _seed_po(tid, *, sku="SKU1", warehouse="bodega norte", qty=200):
    row = query_one(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, sku_count, total_units, total_value, reception_status)
           VALUES (%s, 'sess-x', 1, %s, %s, 'pending') RETURNING id""",
        (tid, qty, qty * 10),
    )
    po_id = row["id"]
    execute(
        """INSERT INTO inventory_po_items
               (po_log_id, tenant_id, sku, display_name, supplier, status,
                recommended_qty, final_qty, unit_cost, warehouse)
           VALUES (%s, %s, %s, %s, 'Proveedor A', 'approved', %s, %s, 10, %s)""",
        (po_id, tid, sku, sku, qty, qty, warehouse),
    )
    return po_id


class _FakeLLM:
    """Returns a queued JSON string per messages.create call."""
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.messages = self

    def create(self, *a, **k):
        text = self._payloads.pop(0)
        block = mock.Mock()
        block.text = text
        resp = mock.Mock()
        resp.content = [block]
        resp.usage = mock.Mock(input_tokens=1, output_tokens=1)
        return resp


def test_is_affirmative():
    assert agent.is_affirmative("sí")
    assert agent.is_affirmative("Si, confirmo")
    assert agent.is_affirmative("dale")
    assert not agent.is_affirmative("no")
    assert not agent.is_affirmative("mejor no")
    assert not agent.is_affirmative("cuánto stock tengo?")


def test_query_turn_dispatches_tool(client, registered_user):
    ctx = _ctx(registered_user)
    _seed_po(ctx.tenant_id, sku="A")
    state = {"history": [], "pending_action": None}
    fake = _FakeLLM([json.dumps({"tool": "list_pending_pos", "args": {}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, "¿qué órdenes tengo pendientes?", state)
    assert "OC" in reply or "pendiente" in reply.lower()
    assert pending is None
    assert history[-1]["role"] == "assistant"


def test_write_proposal_turn_does_not_mutate(client, registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id)
    state = {"history": [], "pending_action": None}
    fake = _FakeLLM([json.dumps({"tool": "approve_po", "args": {"po_log_id": po_id}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, f"aprueba la orden {po_id}", state)
    assert pending is not None and pending["type"] == "approve_po"
    assert "confirm" in reply.lower()
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None  # proposal turn mutated nothing


def test_confirmation_turn_executes_without_llm(client, registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id)
    state = {"history": [], "pending_action": {"type": "approve_po", "po_log_id": po_id}}
    # No LLM patch: confirmation must NOT call the LLM. If it does, this errors.
    reply, history, pending = agent.run_turn(ctx, "sí, confirmo", state)
    assert pending is None
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is not None


def test_non_confirming_message_discards_pending(client, registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id)
    state = {"history": [], "pending_action": {"type": "approve_po", "po_log_id": po_id}}
    fake = _FakeLLM([json.dumps({"tool": "semaphore_status", "args": {}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, "no, mejor muéstrame el semáforo", state)
    # Pending discarded, nothing approved.
    assert pending is None
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None


def test_reception_full_cycle_credits_warehouse(client, registered_user):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id, sku="SKU1", warehouse="bodega norte", qty=200)
    # Turn 1: propose.
    state = {"history": [], "pending_action": None}
    fake = _FakeLLM([json.dumps({"tool": "register_reception",
                                 "args": {"sku": "SKU1", "warehouse": "bodega norte", "quantity": 200}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, "llegaron 200 de SKU1 a bodega norte", state)
    assert pending["type"] == "register_reception"
    stock = query_one("SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku='SKU1' AND warehouse='bodega norte'",
                      (ctx.tenant_id,))
    assert stock is None  # not yet
    # Turn 2: confirm (no LLM).
    state2 = {"history": history, "pending_action": pending}
    reply2, history2, pending2 = agent.run_turn(ctx, "sí", state2)
    assert pending2 is None
    stock = query_one("SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku='SKU1' AND warehouse='bodega norte'",
                      (ctx.tenant_id,))
    assert stock is not None and float(stock["current_stock"]) == 200.0


def test_viewer_write_intent_denied(client, registered_user):
    ctx = _ctx(registered_user, role="viewer")
    po_id = _seed_po(ctx.tenant_id)
    state = {"history": [], "pending_action": None}
    fake = _FakeLLM([json.dumps({"tool": "approve_po", "args": {"po_log_id": po_id}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, f"aprueba {po_id}", state)
    assert pending is None
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None


def test_llm_failure_is_safe(client, registered_user):
    ctx = _ctx(registered_user)
    state = {"history": [], "pending_action": None}

    class _Boom:
        messages = None
        def create(self, *a, **k):
            raise RuntimeError("llm down")
    boom = _Boom(); boom.messages = boom
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=boom):
        reply, history, pending = agent.run_turn(ctx, "hola", state)
    assert isinstance(reply, str) and len(reply) > 0
    assert pending is None

"""The WhatsApp agent: the confirmation gate, and fresh questions answered by the
shared assistant core. The LLM is a scripted fake.

The bot used to route each message through its own JSON prompt to three canned
query tools or a write proposal. It now answers through `backend/assistant/`
(read-only by charter), so what is pinned here is what WhatsApp still owns: the
gate for stored pending actions, the suspension of the two irreversible writes,
and that a model asking for a write gets nothing executed.
"""
import json

import pytest

from backend.ai.local_llm import _ContentBlock, _LLMResponse, _ToolCall
from backend.db.connection import query_one, execute
from backend.whatsapp import agent
from backend.whatsapp import tools as wt
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
    """Each item is one model call: text, or a list of (tool, args) calls."""
    def __init__(self, script):
        self._script = list(script)
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        item = self._script.pop(0)
        if isinstance(item, list):
            calls = [_ToolCall(id=f"c{i}", name=n, arguments=json.dumps(a))
                     for i, (n, a) in enumerate(item)]
            return _LLMResponse(content=[_ContentBlock(text="")], tool_calls=calls)
        return _LLMResponse(content=[_ContentBlock(text=item)])


@pytest.fixture
def llm(monkeypatch):
    def install(script):
        fake = _FakeLLM(script)
        monkeypatch.setattr("backend.ai.local_llm.get_local_llm_client", lambda *a, **k: fake)
        return fake
    return install


def test_is_affirmative():
    assert agent.is_affirmative("sí")
    assert agent.is_affirmative("Si, confirmo")
    assert agent.is_affirmative("dale")
    assert not agent.is_affirmative("no")
    assert not agent.is_affirmative("mejor no")
    assert not agent.is_affirmative("cuánto stock tengo?")


def test_a_question_is_answered_by_the_core_with_its_tools(client, registered_user, llm):
    ctx = _ctx(registered_user)
    _seed_po(ctx.tenant_id, sku="A")
    fake = llm([[("list_purchase_orders", {"status": "open"})],
                "Tienes una orden pendiente por 200 unidades."])
    reply, history, pending = agent.run_turn(
        ctx, "¿qué órdenes tengo pendientes?", {"history": [], "pending_action": None})
    assert reply == "Tienes una orden pendiente por 200 unidades."
    assert pending is None
    assert history[-1] == {"role": "assistant", "content": reply}
    # The tool result the model read is the tenant's own order.
    tool_msg = fake.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and '"total_units": 200' in tool_msg["content"]


def test_the_history_reaches_the_model(client, registered_user, llm):
    ctx = _ctx(registered_user)
    fake = llm(["De nada."])
    state = {"history": [{"role": "user", "content": "hola"},
                         {"role": "assistant", "content": "¡Hola!"}], "pending_action": None}
    agent.run_turn(ctx, "gracias", state)
    sent = fake.calls[0]["messages"]
    assert sent[:2] == state["history"] and sent[-1] == {"role": "user", "content": "gracias"}


def test_confirmation_turn_executes_without_llm(client, registered_user, monkeypatch):
    """The gate for a stored, confirmable action: a bare 'sí' runs it without
    any model call."""
    monkeypatch.setattr(wt, "execute_pending_action", lambda ctx, action: "HECHO ✅")
    ctx = _ctx(registered_user)
    state = {"history": [], "pending_action": {"type": "fake_reversible_write"}}

    def _no_llm(*a, **k):
        raise AssertionError("confirmation must NOT call the LLM")
    monkeypatch.setattr("backend.ai.local_llm.get_local_llm_client", _no_llm)
    reply, history, pending = agent.run_turn(ctx, "sí, confirmo", state)
    assert pending is None
    assert reply == "HECHO ✅"


class TestSuspendedWrites:
    """`approve_po` and `register_reception` are out of WRITE_TOOLS until
    receive_po and mark_po_sent have inverses — and the assistant core offers
    no write at all. These pin that a model asking for one executes nothing."""

    def test_a_model_calling_approve_po_executes_nothing(self, client, registered_user, llm):
        ctx = _ctx(registered_user)
        po_id = _seed_po(ctx.tenant_id)
        fake = llm([[("approve_po", {"po_log_id": po_id})],
                    "Eso se hace en /pedidos."])
        reply, history, pending = agent.run_turn(
            ctx, f"aprueba la orden {po_id}", {"history": [], "pending_action": None})
        assert pending is None
        assert "Unknown tool" in fake.calls[1]["messages"][-1]["content"]
        row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
        assert row["sent_at"] is None

    def test_a_reception_intent_credits_no_stock(self, client, registered_user, llm):
        ctx = _ctx(registered_user)
        _seed_po(ctx.tenant_id, sku="SKU1", warehouse="bodega norte", qty=200)
        llm([[("register_reception", {"sku": "SKU1", "warehouse": "bodega norte", "quantity": 200})],
             "Regístralo en /pedidos."])
        reply, history, pending = agent.run_turn(
            ctx, "llegaron 200 de SKU1 a bodega norte", {"history": [], "pending_action": None})
        assert pending is None
        assert query_one(
            "SELECT current_stock FROM inventory_stock "
            "WHERE tenant_id=%s AND sku='SKU1' AND warehouse='bodega norte'",
            (ctx.tenant_id,),
        ) is None

    def test_a_pending_proposal_stored_before_the_suspension_does_not_fire(
        self, client, registered_user
    ):
        """The dangerous leftover: a proposal created by the old code is still
        in the conversation store, and the user answers 'sí' today."""
        ctx = _ctx(registered_user)
        po_id = _seed_po(ctx.tenant_id)
        state = {"history": [], "pending_action": {"type": "approve_po", "po_log_id": po_id}}
        reply, history, pending = agent.run_turn(ctx, "sí, confirmo", state)
        assert pending is None
        row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
        assert row["sent_at"] is None, "a stale pending action executed an irreversible write"


def test_non_confirming_message_discards_pending(client, registered_user, llm):
    ctx = _ctx(registered_user)
    po_id = _seed_po(ctx.tenant_id)
    state = {"history": [], "pending_action": {"type": "approve_po", "po_log_id": po_id}}
    llm(["Tu semáforo está al día."])
    reply, history, pending = agent.run_turn(ctx, "no, mejor muéstrame el semáforo", state)
    assert pending is None
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None


def test_viewer_gets_answers_and_no_write(client, registered_user, llm):
    ctx = _ctx(registered_user, role="viewer")
    po_id = _seed_po(ctx.tenant_id)
    llm([[("approve_po", {"po_log_id": po_id})], "No puedo hacerlo desde aquí."])
    reply, history, pending = agent.run_turn(ctx, f"aprueba {po_id}", {"history": [], "pending_action": None})
    assert pending is None and reply
    row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
    assert row["sent_at"] is None


def test_llm_failure_is_safe(client, registered_user, monkeypatch):
    """The model failing is not an apology: the core answers from the data by
    rules and says so."""
    from backend.notifications.locale import render_es
    ctx = _ctx(registered_user)

    class _Boom:
        def __init__(self):
            self.messages = self

        def create(self, *a, **k):
            raise RuntimeError("llm down")
    monkeypatch.setattr("backend.ai.local_llm.get_local_llm_client", lambda *a, **k: _Boom())
    reply, history, pending = agent.run_turn(ctx, "hola", {"history": [], "pending_action": None})
    assert render_es("assistant_intro_failed") in reply
    assert "llm down" not in reply
    assert pending is None

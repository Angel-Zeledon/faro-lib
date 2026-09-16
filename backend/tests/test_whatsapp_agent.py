"""Agent: routing + the two-step confirmation gate. LLM is mocked."""
import json
from unittest import mock

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


class TestRouterParsing:
    """`_JSON_RE = re.compile(r"\\{.*\\}", re.DOTALL)` was greedy: it took from
    the first brace in the model's answer to the LAST one. Prose with a brace
    in it, or two objects, produced a slice that does not parse — and the turn
    then fell through to the help menu with no log line, so a user who asked
    to register a reception got a menu and nobody could measure how often."""

    def test_prose_before_the_object_does_not_break_it(self):
        raw = 'Claro, uso {la herramienta} adecuada:\n{"tool": "semaphore_status", "args": {}}'
        assert agent._first_json_object(raw) == {"tool": "semaphore_status", "args": {}}

    def test_two_objects_take_the_first(self):
        raw = '{"tool": "semaphore_status", "args": {}} {"tool": null}'
        assert agent._first_json_object(raw)["tool"] == "semaphore_status"

    def test_trailing_prose_after_the_object(self):
        raw = '{"tool": null, "reply": "hola"}\n\nEspero que ayude.'
        assert agent._first_json_object(raw) == {"tool": None, "reply": "hola"}

    def test_nested_objects_survive(self):
        raw = '{"tool": "forecast_summary", "args": {"sku": "A-1"}}'
        assert agent._first_json_object(raw)["args"] == {"sku": "A-1"}

    def test_no_object_at_all_is_none(self):
        assert agent._first_json_object("lo siento, no entendí") is None

    def test_an_unparseable_answer_is_logged(self, client, registered_user, caplog):
        """The fallback is fine; the silence was not."""
        ctx = _ctx(registered_user)
        fake = _FakeLLM(["no pude, perdón"])
        with caplog.at_level("WARNING"):
            with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
                agent.run_turn(ctx, "registra una recepción", {"history": [], "pending_action": None})
        assert any("carried no JSON object" in r.getMessage() for r in caplog.records), \
            "the router failed and left no trace"


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


_FAKE_WRITE = "fake_reversible_write"


def _register_a_reversible_write_tool(monkeypatch):
    """A stand-in write tool for the tests that are about the CONFIRMATION
    GATE itself rather than about approving a PO.

    The gate used to be exercised through `approve_po`, which is suspended
    (see whatsapp/tools.py WRITE_TOOLS). Testing the machinery through the
    action that is currently switched off would have deleted the coverage of
    the machinery along with it — and the gate is what makes re-enabling the
    real tools safe later."""
    monkeypatch.setitem(wt.WRITE_TOOLS, _FAKE_WRITE,
                        lambda ctx, args: {"type": _FAKE_WRITE,
                                           "summary": "Haré algo. ¿Confirmas? (responde SÍ)"})
    monkeypatch.setattr(wt, "execute_pending_action", lambda ctx, action: "HECHO ✅")


def test_write_proposal_turn_does_not_mutate(client, registered_user, monkeypatch):
    _register_a_reversible_write_tool(monkeypatch)
    ctx = _ctx(registered_user)
    state = {"history": [], "pending_action": None}
    fake = _FakeLLM([json.dumps({"tool": _FAKE_WRITE, "args": {}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, "hazlo", state)
    assert pending is not None and pending["type"] == _FAKE_WRITE
    assert "confirm" in reply.lower()


def test_confirmation_turn_executes_without_llm(client, registered_user, monkeypatch):
    _register_a_reversible_write_tool(monkeypatch)
    ctx = _ctx(registered_user)
    state = {"history": [], "pending_action": {"type": _FAKE_WRITE}}
    # No LLM patch: confirmation must NOT call the LLM. If it does, this errors.
    reply, history, pending = agent.run_turn(ctx, "sí, confirmo", state)
    assert pending is None
    assert reply == "HECHO ✅"


class TestSuspendedWrites:
    """`approve_po` and `register_reception` are out of WRITE_TOOLS until
    receive_po and mark_po_sent have inverses: a WhatsApp turn is the one
    surface with no confirmation screen, no undo and no visible audit, and
    both of them move stock, learned lead times and the cash calendar for
    good. These tests are the lock on that, and they are also the exact tests
    to delete when the undo work lands."""

    def test_an_approval_intent_is_answered_not_executed(self, client, registered_user):
        ctx = _ctx(registered_user)
        po_id = _seed_po(ctx.tenant_id)
        state = {"history": [], "pending_action": None}
        fake = _FakeLLM([json.dumps({"tool": "approve_po", "args": {"po_log_id": po_id}})])
        with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
            reply, history, pending = agent.run_turn(ctx, f"aprueba la orden {po_id}", state)
        # No proposal is stored, so no later "sí" can execute it either.
        assert pending is None
        assert "app" in reply.lower()
        row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
        assert row["sent_at"] is None

    def test_a_reception_intent_credits_no_stock(self, client, registered_user):
        ctx = _ctx(registered_user)
        _seed_po(ctx.tenant_id, sku="SKU1", warehouse="bodega norte", qty=200)
        state = {"history": [], "pending_action": None}
        fake = _FakeLLM([json.dumps({
            "tool": "register_reception",
            "args": {"sku": "SKU1", "warehouse": "bodega norte", "quantity": 200},
        })])
        with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
            reply, history, pending = agent.run_turn(
                ctx, "llegaron 200 de SKU1 a bodega norte", state)
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


def test_two_turn_cycle_proposes_then_executes(client, registered_user, monkeypatch):
    """The full shape of a write over WhatsApp — propose, then execute on a
    bare 'sí' with no second LLM call — through the reversible stand-in. Was
    `test_reception_full_cycle_credits_warehouse`; that receive_po path is
    still covered directly in test_whatsapp_tools.py, where it belongs: it is
    the executor that works, not the chat route to it."""
    executed = []
    monkeypatch.setitem(wt.WRITE_TOOLS, _FAKE_WRITE,
                        lambda ctx, args: {"type": _FAKE_WRITE, "n": args.get("n"),
                                           "summary": "¿Confirmas? (responde SÍ)"})
    monkeypatch.setattr(wt, "execute_pending_action",
                        lambda ctx, action: executed.append(action) or "HECHO ✅")

    ctx = _ctx(registered_user)
    state = {"history": [], "pending_action": None}
    fake = _FakeLLM([json.dumps({"tool": _FAKE_WRITE, "args": {"n": 200}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, "hazlo con 200", state)
    assert pending["type"] == _FAKE_WRITE
    assert executed == []  # the proposal turn executes nothing

    state2 = {"history": history, "pending_action": pending}
    reply2, history2, pending2 = agent.run_turn(ctx, "sí", state2)
    assert pending2 is None
    assert [a["n"] for a in executed] == [200]


def test_viewer_write_intent_denied(client, registered_user, monkeypatch):
    """A viewer must not even get a proposal stored. Exercised through the
    stand-in so it keeps testing the ROLE GATE rather than the suspension."""
    _register_a_reversible_write_tool(monkeypatch)
    ctx = _ctx(registered_user, role="viewer")
    state = {"history": [], "pending_action": None}
    fake = _FakeLLM([json.dumps({"tool": _FAKE_WRITE, "args": {}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        reply, history, pending = agent.run_turn(ctx, "hazlo", state)
    assert pending is None
    assert "lectura" in reply.lower()


def test_viewer_write_intent_denied_for_suspended_tools_too(client, registered_user):
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

"""
Generic-mode stopgap for the WhatsApp bot: when whatsapp_bot_generic_mode is on
(no hosted LLM funded), a fresh message must get a fast canned reply WITHOUT the
LLM being called, while confirmations still execute deterministically.
"""
from backend.notifications.locale import render_es
from backend.whatsapp import agent as A
from backend.whatsapp import tools as wt
from backend.whatsapp.tools import ToolContext

# The canned reply is copy, so it lives in the backend catalog, not as a module
# constant on the agent. Reading it through the same key the agent uses keeps
# this test about the BEHAVIOUR (no LLM call) rather than about the wording.
_BASIC_MODE = render_es("wa_generic_mode")


def _ctx():
    return ToolContext(tenant_id="t1", user_id="u1", role="analyst")


def test_generic_mode_replies_without_calling_the_llm(monkeypatch):
    monkeypatch.setattr("backend.config.settings.whatsapp_bot_generic_mode", True)

    def _boom(*a, **k):
        raise AssertionError("the LLM router must NOT be called in generic mode")

    monkeypatch.setattr(A, "_route", _boom)

    reply, history, pending = A.run_turn(
        _ctx(), "dame el semaforo de inventario",
        {"history": [], "pending_action": None})

    assert reply == _BASIC_MODE
    assert pending is None
    # The turn is still recorded.
    assert history[-2]["content"] == "dame el semaforo de inventario"
    assert history[-1]["content"] == _BASIC_MODE


def test_generic_mode_still_executes_a_confirmation(monkeypatch):
    monkeypatch.setattr("backend.config.settings.whatsapp_bot_generic_mode", True)
    # LLM must not be involved even when confirming.
    monkeypatch.setattr(A, "_route", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no LLM on a confirmation turn")))
    monkeypatch.setattr(wt, "execute_pending_action", lambda ctx, action: "DONE ✓")

    pending = {"type": "approve_po", "po_log_id": "po1", "summary": "..."}
    reply, history, new_pending = A.run_turn(
        _ctx(), "sí", {"history": [], "pending_action": pending})

    assert reply == "DONE ✓"
    assert new_pending is None


def test_smart_mode_is_the_default_off(monkeypatch):
    """With the flag off (the test default), the bot routes via the LLM — the
    generic branch must not swallow the turn."""
    # conftest resets the flag to False; prove routing is reached.
    called = {}

    def _fake_route(ctx, text, history):
        called["yes"] = True
        return {"tool": None, "args": {}, "reply": "hola"}

    monkeypatch.setattr(A, "_route", _fake_route)
    reply, _h, _p = A.run_turn(_ctx(), "hola", {"history": [], "pending_action": None})
    assert called.get("yes") is True
    assert reply == "hola"

"""The assistant core (`backend/assistant/`): it answers from THIS account, never
another; it only reads; it degrades to rule-based text without a model; every
figure it states is checked; and each channel gets its own shape.

The model is always a scripted fake (conftest patches the factory suite-wide
anyway): these tests are about what we send it and what we do with what comes
back, not about DeepSeek.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import textwrap
from uuid import uuid4

import pytest

from backend.ai.local_llm import LLMNotConfigured, _ContentBlock, _LLMResponse, _ToolCall
from backend.auth.guards import CurrentUser
from backend.db.connection import execute, query_one


# ── A scripted model ─────────────────────────────────────────────────────────

class FakeLLM:
    """Each script item is one model call: a string (final text), a list of
    (tool_name, args) (tool calls) or an exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, list):
            calls = [_ToolCall(id=f"call_{len(self.calls)}_{i}", name=n, arguments=json.dumps(a))
                     for i, (n, a) in enumerate(item)]
            return _LLMResponse(
                content=[_ContentBlock(text="")], tool_calls=calls,
                message={"role": "assistant", "content": "", "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": c.arguments}} for c in calls]},
            )
        return _LLMResponse(content=[_ContentBlock(text=item)])


@pytest.fixture
def fake_llm(monkeypatch):
    def install(script):
        fake = FakeLLM(script)
        monkeypatch.setattr("backend.ai.local_llm.get_local_llm_client",
                            lambda *a, **k: fake)
        return fake
    return install


# ── Two real accounts ────────────────────────────────────────────────────────

def _seed_account(tid: str, uid: str, *, tag: str) -> dict:
    """Stock, a supplier, an open purchase order and an activity row, all named
    with `tag` so a leak across tenants is a plain substring check."""
    from backend.activity.service import log_action
    from backend.inventory import service as inv_svc
    from backend.inventory import supplier_service

    product = f"Producto {tag} Reservado"
    supplier = f"Proveedor {tag}"
    inv_svc.upsert_stock(tid, f"{tag.upper()}-1", {
        "display_name": product, "current_stock": 37, "unit_cost": 12.5,
        "supplier": supplier, "lead_time_days": 9,
    })
    supplier_service.create_supplier(tid, {"name": supplier, "lead_time_days": 9})
    po = query_one(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, sku_count, total_units, total_value, reception_status)
           VALUES (%s, 'sess-x', 1, 120, 4324, 'pending') RETURNING id, po_number""",
        (tid,),
    )
    execute(
        """INSERT INTO inventory_po_items
               (po_log_id, tenant_id, sku, display_name, supplier, status,
                recommended_qty, final_qty, unit_cost, warehouse)
           VALUES (%s, %s, %s, %s, %s, 'approved', 120, 120, 36.03, 'principal')""",
        (po["id"], tid, f"{tag.upper()}-1", product, supplier),
    )
    log_action(tid, uid, f"{tag.lower()}_marker_action")
    return {"product": product, "supplier": supplier, "po_id": po["id"],
            "po_number": po["po_number"], "sku": f"{tag.upper()}-1"}


@pytest.fixture
def accounts(client, registered_user):
    from backend.tenants.data_export import delete_tenant
    from backend.tenants.service import create_tenant
    from backend.users import service as user_svc

    a_tid = registered_user["tenant"]["id"]
    a_uid = registered_user["user"]["id"]
    a = _seed_account(a_tid, a_uid, tag="Alfa")

    b_tenant = create_tenant(f"Beta Corp {uuid4().hex[:6]}")
    b_user = user_svc.create_user(
        tenant_id=b_tenant["id"], email=f"bruno-{uuid4().hex[:8]}@example.com",
        password="TestPass123!", role="admin", full_name="Bruno Beta",
    )
    b = _seed_account(b_tenant["id"], b_user["id"], tag="Beta")
    yield {
        "a": {**a, "tid": a_tid, "uid": a_uid, "company": registered_user["tenant"]["name"]},
        "b": {**b, "tid": b_tenant["id"], "uid": b_user["id"], "company": b_tenant["name"]},
    }
    delete_tenant(b_tenant["id"])
    delete_tenant(a_tid)


def _data(acct, role="admin"):
    from backend.assistant.account import AccountData
    return AccountData(CurrentUser(acct["uid"], acct["tid"], role))


# ── The context is this account's, and only this account's ───────────────────

class TestAccountContext:

    def test_it_carries_the_users_own_account(self, accounts):
        from backend.assistant.context import build_account_context
        from backend.inventory.roi_service import format_po_number
        a = accounts["a"]
        ctx = build_account_context(_data(a), "¿qué pasa con mis pedidos?",
                                    language="es", channel="web")
        assert ctx.first_name == "Test"
        assert "first name: Test" in ctx.text
        assert a["company"] in ctx.text
        assert format_po_number(a["po_number"], a["po_id"]) in ctx.text
        assert "₡4,324" in ctx.text, "the open order's value, in the tenant's currency"
        assert "alfa_marker_action" in ctx.text, "what the user did recently"
        assert "No completed forecast yet" in ctx.text, \
            "a missing forecast is stated, not left as an empty (=fine) section"
        assert ctx.sections[0] == "identity"

    def test_it_never_carries_another_tenants_data(self, accounts):
        from backend.assistant.context import build_account_context
        a, b = accounts["a"], accounts["b"]
        for me, other in ((a, b), (b, a)):
            ctx = build_account_context(_data(me), f"¿cómo va {other['product']}?",
                                        language="es", channel="web")
            for leak in (other["product"], other["supplier"], other["company"], other["sku"],
                         f"{other['sku'].split('-')[0].lower()}_marker_action"):
                assert leak not in ctx.text, f"{leak!r} leaked into {me['company']}'s context"
            assert me["company"] in ctx.text
            assert ctx.focus_skus == [], "another tenant's product resolved as a mention"

    def test_a_named_product_is_resolved_against_this_catalogue(self, accounts):
        from backend.assistant.context import find_mentioned_skus
        a = accounts["a"]
        assert find_mentioned_skus(_data(a), f"¿por qué {a['product'].lower()} está así?") == [a["sku"]]

    def test_the_question_ranks_the_sections(self, accounts):
        from backend.assistant.context import build_account_context
        data = _data(accounts["a"])
        about_orders = build_account_context(data, "¿llegó mi pedido?", language="es", channel="web")
        about_activity = build_account_context(data, "¿qué hice últimamente?", language="es", channel="web")
        assert about_orders.sections.index("orders") < about_orders.sections.index("activity")
        assert about_activity.sections.index("activity") < about_activity.sections.index("orders")

    def test_the_budget_cuts_whole_lines_and_says_so(self, accounts):
        from backend.assistant.context import build_account_context
        ctx = build_account_context(_data(accounts["a"]), "hola", language="es",
                                    channel="web", max_chars=400)
        assert len(ctx.text) <= 400 + 200
        assert ctx.sections[0] == "identity"


class TestSemaforoReachesTheContext:
    """With a completed forecast and stock, the red/amber products come with
    their quantities, cover and supplier — the same rows /compras shows."""

    def test_risks_carry_quantities_and_supplier(self, client, registered_user, completed_session):
        from backend.assistant.context import build_account_context
        from backend.api.v1 import inventory as inventory_router
        from backend.inventory import service as inv_svc
        from backend.tenants.data_export import delete_tenant
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        try:
            inv_svc.upsert_stock(tid, "SKU_001", {"display_name": "Aceite Piloto", "current_stock": 0,
                                                  "unit_cost": 3, "supplier": "Andina Piloto",
                                                  "lead_time_days": 30})
            user = CurrentUser(uid, tid, "admin")
            screen = inventory_router.morning_briefing(session_id=None, service_level=0.95,
                                                       user=user)["data"]
            red = [i for i in screen["risks"] + screen["warnings"] if i["sku"] == "SKU_001"]
            assert red, "fixture did not put SKU_001 at risk; the test would prove nothing"
            ctx = build_account_context(_data({"tid": tid, "uid": uid}),
                                        "¿qué debo pedir?", language="es", channel="web")
            assert "Aceite Piloto [SKU_001]" in ctx.text
            assert "supplier Andina Piloto" in ctx.text
            assert f"order {red[0]['recommended_qty']:,.0f} units" in ctx.text
        finally:
            delete_tenant(tid)


# ── Tools: read-only, tenant-bound ───────────────────────────────────────────

class TestToolsOnlyRead:

    def test_every_account_read_resolves_to_a_GET_route(self):
        """The wall. Every router function `AccountData` calls is resolved to the
        FastAPI route it backs, and the methods must be exactly {GET}. Anything
        else it calls must be one of the two named SELECT helpers."""
        from backend.assistant import account as account_mod
        from backend.main import app

        methods_of: dict = {}
        for route in app.routes:
            endpoint = getattr(route, "endpoint", None)
            if endpoint is not None:
                methods_of.setdefault(endpoint, set()).update(getattr(route, "methods", None) or set())

        allowed_service_reads = {"backend.tenants.service.get_tenant",
                                 "backend.preferences.service.get_preferences",
                                 "backend.formatting.money"}
        tree = ast.parse(textwrap.dedent(inspect.getsource(account_mod)))
        checked = 0
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef,))]:
            aliases = {}
            for node in ast.walk(fn):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for name in node.names:
                        aliases[name.asname or name.name] = (node.module, name.name)
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in aliases:
                    module_name, attr = aliases[f.value.id]
                    module = importlib.import_module(f"{module_name}.{attr}")
                    target = getattr(module, f.attr)
                    methods = methods_of.get(target)
                    assert methods == {"GET"}, f"AccountData.{fn.name} calls {attr}.{f.attr} ({methods})"
                    checked += 1
                elif isinstance(f, ast.Name) and f.id in aliases:
                    module_name, attr = aliases[f.id]
                    assert f"{module_name}.{attr}" in allowed_service_reads, \
                        f"AccountData.{fn.name} calls {module_name}.{attr}, not a GET route"
        assert checked >= 14, f"only {checked} router calls resolved — the check is not reading them"

    def test_no_assistant_module_touches_the_database_directly(self):
        """Tools, context and core reach data only through AccountData."""
        for mod in ("tools", "context", "core", "welcome", "grounding", "channels"):
            src = inspect.getsource(importlib.import_module(f"backend.assistant.{mod}"))
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("backend.db"), \
                        f"backend/assistant/{mod}.py imports {node.module}"

    def test_the_catalogue_has_no_write_shaped_tool(self):
        from backend.assistant.tools import TOOLS
        for t in TOOLS:
            assert t.name.split("_")[0] in ("find", "get", "list"), t.name

    def test_an_unknown_tool_runs_nothing(self, accounts):
        from backend.assistant.tools import run_tool
        a = accounts["a"]
        out, ok = run_tool(_data(a), "approve_po", json.dumps({"po_log_id": a["po_id"]}))
        assert not ok and "Unknown tool" in out
        row = query_one("SELECT sent_at, reception_status FROM inventory_po_log WHERE id = %s", (a["po_id"],))
        assert row["sent_at"] is None and row["reception_status"] == "pending"

    def test_tools_answer_from_this_tenant_only(self, accounts):
        from backend.assistant.tools import run_tool
        a, b = accounts["a"], accounts["b"]
        out, ok = run_tool(_data(a), "find_products", {"query": "Beta"})
        assert ok and json.loads(out)["matches"] == []
        out, ok = run_tool(_data(a), "find_products", {"query": "Alfa"})
        assert [m["sku"] for m in json.loads(out)["matches"]] == [a["sku"]]
        out, _ = run_tool(_data(a), "get_supplier", {"name": b["supplier"]})
        assert json.loads(out)["found"] is False
        out, _ = run_tool(_data(a), "get_purchase_order", {"reference": str(b["po_number"])})
        assert b["product"] not in out

    def test_malformed_arguments_go_back_to_the_model(self, accounts):
        from backend.assistant.tools import run_tool
        out, ok = run_tool(_data(accounts["a"]), "find_products", "{not json")
        assert not ok and "error" in json.loads(out)


# ── The core: no key, a slow model, the tool loop, the grounding guard ───────

class TestCore:

    def test_without_a_key_it_answers_from_the_data_by_rules(self, accounts, monkeypatch):
        from backend.assistant import answer
        from backend.notifications.locale import render

        def _no_key(*a, **k):
            raise LLMNotConfigured("DEEPSEEK_API_KEY is not set")
        monkeypatch.setattr("backend.ai.local_llm.get_local_llm_client", _no_key)
        a = accounts["a"]
        reply = answer(a["tid"], a["uid"], "web", "¿qué compro hoy?", [], role="admin", language="es")
        assert reply.source == "rules" and reply.reason == "not_configured"
        assert reply.text.startswith("Hola Test. " + render("es", "assistant_intro_not_configured"))
        assert render("es", "assistant_no_forecast") in reply.text

        reply_en = answer(a["tid"], a["uid"], "web", "what do I buy?", [], language="en")
        assert reply_en.text.startswith("Hi Test. ")

    def test_a_timeout_is_named_as_such(self, accounts, fake_llm):
        import httpx
        from backend.assistant import answer
        fake_llm([httpx.ReadTimeout("slow")])
        a = accounts["a"]
        reply = answer(a["tid"], a["uid"], "web", "hola", [], language="es")
        assert reply.source == "rules" and reply.reason == "timeout"

    def test_no_time_left_means_no_model_call(self, accounts, fake_llm):
        from backend.assistant import answer
        fake = fake_llm(["never sent"])
        a = accounts["a"]
        reply = answer(a["tid"], a["uid"], "web", "hola", [], language="es", budget_s=0.5)
        assert reply.source == "rules" and fake.calls == []

    def test_the_prompt_is_personal_and_in_the_users_language(self, accounts, fake_llm):
        from backend.assistant import answer
        fake = fake_llm(["Claro, Test."])
        a = accounts["a"]
        reply = answer(a["tid"], a["uid"], "web", "hola", [{"role": "user", "content": "antes"},
                                                           {"role": "assistant", "content": "ok"}],
                       language="en")
        system = fake.calls[0]["system"]
        assert "for Test at " + a["company"] in system
        assert "always answer in English" in system
        assert "ACCOUNT DATA" in system and "₡4,324" in system
        assert fake.calls[0]["messages"][0] == {"role": "user", "content": "antes"}
        assert [t["function"]["name"] for t in fake.calls[0]["tools"]][:2] == ["find_products", "get_product"]
        assert reply.source == "assistant" and reply.text == "Claro, Test."

    def test_the_tool_loop_feeds_results_back(self, accounts, fake_llm):
        from backend.assistant import answer
        a = accounts["a"]
        fake = fake_llm([[("find_products", {"query": "Alfa"})],
                         f"Encontré {a['product']}."])
        reply = answer(a["tid"], a["uid"], "web", "busca alfa", [], language="es")
        assert reply.tools_used == ["find_products"]
        second = fake.calls[1]["messages"]
        assert second[-2]["tool_calls"][0]["function"]["name"] == "find_products"
        assert second[-1]["role"] == "tool" and a["sku"] in second[-1]["content"]
        assert reply.text == f"Encontré {a['product']}."

    def test_a_grounded_figure_passes(self, accounts, fake_llm):
        from backend.assistant import answer
        a = accounts["a"]
        fake_llm(["Tu orden abierta suma ₡4.324 por 120 unidades."])
        reply = answer(a["tid"], a["uid"], "web", "¿cuánto suma mi orden?", [], language="es")
        assert reply.grounded and reply.unverified == []

    def test_an_invented_figure_is_rewritten(self, accounts, fake_llm):
        from backend.assistant import answer
        a = accounts["a"]
        fake = fake_llm(["Pide 780 unidades.", "No tengo esa cifra; revisa /compras."])
        reply = answer(a["tid"], a["uid"], "web", "¿cuánto pido?", [], language="es")
        assert "780" in fake.calls[1]["messages"][-1]["content"], "the rewrite names the bad figure"
        assert reply.grounded and reply.text == "No tengo esa cifra; revisa /compras."

    def test_a_figure_that_survives_the_rewrite_is_flagged_to_the_user(self, accounts, fake_llm):
        from backend.assistant import answer
        from backend.notifications.locale import render
        a = accounts["a"]
        fake_llm(["Pide 780 unidades.", "Pide 780 unidades, seguro."])
        reply = answer(a["tid"], a["uid"], "web", "¿cuánto pido?", [], language="es")
        assert not reply.grounded and reply.unverified == ["780"]
        assert reply.text.endswith(render("es", "assistant_unverified", numbers="780"))


class TestGrounding:

    @pytest.mark.parametrize("reply", [
        "Tienes ₡25,430 parados", "Tienes ₡25.430 parados", "Tienes 25 430 parados",
        "unos 25 mil", "cubre 0.2 semanas", "precisión de 86%", "vendes 164 por día",
    ])
    def test_presentations_of_a_real_figure_pass(self, reply):
        from backend.assistant.grounding import unverified_numbers
        evidence = "capital 25430.0; cover 0.2 week(s); accuracy 0.8574; daily 164.4087"
        assert unverified_numbers(reply, evidence) == []

    @pytest.mark.parametrize("reply,bad", [
        ("Pide 780 unidades", "780"), ("Tienes ₡26,000 parados", "26,000"), ("vendes 12.5%", "12.5%"),
    ])
    def test_a_figure_not_in_the_evidence_is_caught(self, reply, bad):
        from backend.assistant.grounding import unverified_numbers
        assert unverified_numbers(reply, "order 78 units; capital 25430; margin 0.3") == [bad]

    def test_codes_dates_and_small_counts_are_not_claims(self):
        from backend.assistant.grounding import unverified_numbers
        reply = "1. La orden OC-000123 del 2026-08-11 para SKU-881 (Aceite 1L) y 3 productos más."
        assert unverified_numbers(reply, "") == []


# ── Channels ─────────────────────────────────────────────────────────────────

class TestChannels:

    def test_whatsapp_is_short_plain_text_with_absolute_links(self, monkeypatch):
        from backend.assistant.channels import get_channel
        from backend.config import settings
        monkeypatch.setattr(settings, "frontend_url", "https://app.example.com")
        ch = get_channel("whatsapp")
        out = ch.format("### Hoy\n**Aceite** está en rojo.\n- pide 315\nVe a [Compras](/compras)\n"
                        "| a | b |\n|---|---|\n| 1 | 2 |\n" + "x" * 3000)
        assert "*Hoy*" in out and "*Aceite*" in out and "**" not in out and "###" not in out
        assert "• pide 315" in out
        assert "Compras: https://app.example.com/compras" in out
        assert ch.format("revisa /pedidos hoy") == "revisa https://app.example.com/pedidos hoy"
        assert "|---|" not in out and "• 1 · 2" in out
        assert len(out) <= ch.max_chars + 2 and out.endswith("…")

    def test_web_keeps_markdown_and_relative_links(self):
        from backend.assistant.channels import get_channel
        out = get_channel("web").format("**Hola** [Compras](/compras)\n| a | b |\n|---|---|\n| 1 | 2 |")
        assert "**Hola** [Compras](/compras)" in out
        assert "|---|" not in out and "- 1 · 2" in out

    def test_each_channel_tells_the_model_how_to_write(self, accounts, fake_llm):
        from backend.assistant import answer
        a = accounts["a"]
        fake = fake_llm(["**Hola** Test"])
        reply = answer(a["tid"], a["uid"], "whatsapp", "hola", [], language="es")
        assert "WhatsApp message" in fake.calls[0]["system"]
        assert fake.calls[0]["max_tokens"] < 600
        assert reply.text == "*Hola* Test"


# ── Wiring: the web chat and the WhatsApp bot both reach the core ────────────

class TestWebChatWiring:

    def test_a_message_is_answered_by_the_core_and_stored(self, client, auth_headers, accounts, fake_llm):
        # The first message of a chat also asks the model for a title.
        fake = fake_llm(["Mi orden", "Hola Test, tu orden está pendiente."])
        chat = client.post("/api/v1/analyst/chats", json={}, headers=auth_headers).json()["data"]
        resp = client.post(f"/api/v1/analyst/chats/{chat['id']}/messages",
                           json={"question": "¿y mi orden?", "language": "es"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert "for Test at" in fake.calls[1]["system"]
        row = query_one("SELECT content, source FROM chat_messages WHERE chat_id = %s AND role = 'assistant'",
                        (chat["id"],))
        assert row == {"content": "Hola Test, tu orden está pendiente.", "source": "assistant"}

    def test_without_a_key_the_chat_stores_the_rules_answer(self, client, auth_headers, accounts, monkeypatch):
        def _no_key(*a, **k):
            raise LLMNotConfigured("no key")
        monkeypatch.setattr("backend.ai.local_llm.get_local_llm_client", _no_key)
        chat = client.post("/api/v1/analyst/chats", json={}, headers=auth_headers).json()["data"]
        resp = client.post(f"/api/v1/analyst/chats/{chat['id']}/messages",
                           json={"question": "hola", "language": "en"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["assistant"]["reason"] == "not_configured"
        row = query_one("SELECT content, source FROM chat_messages WHERE chat_id = %s AND role = 'assistant'",
                        (chat["id"],))
        assert row["source"] == "rules" and row["content"].startswith("Hi Test. ")

    def test_welcome_is_personal(self, client, auth_headers, accounts):
        resp = client.get("/api/v1/analyst/welcome", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["first_name"] == "Test" and data["company"] == accounts["a"]["company"]
        assert data["suggestions"] == [{"code": "how_to_start", "params": {}}]

    def test_every_suggestion_code_has_copy_in_both_languages(self):
        """The backend sends codes; a code with no `analyst.suggest.<code>` entry
        renders as a raw key on a button the user is invited to press."""
        import pathlib
        import re
        from backend.assistant import welcome
        codes = set(re.findall(r'"code": "(\w+)"', inspect.getsource(welcome)))
        assert len(codes) >= 8, codes
        catalogue = (pathlib.Path(__file__).resolve().parents[2]
                     / "Frontend" / "src" / "i18n" / "translations.ts").read_text(encoding="utf-8")
        for code in codes:
            assert catalogue.count(f"'analyst.suggest.{code}':") == 2, f"{code} not in es+en"

    def test_welcome_suggestions_come_from_the_accounts_risks(self, client, auth_headers,
                                                               registered_user, completed_session):
        from backend.inventory import service as inv_svc
        tid = registered_user["tenant"]["id"]
        inv_svc.upsert_stock(tid, "SKU_001", {"display_name": "Aceite Piloto", "current_stock": 0,
                                              "unit_cost": 3, "supplier": "Andina Piloto",
                                              "lead_time_days": 30})
        data = client.get("/api/v1/analyst/welcome", headers=auth_headers).json()["data"]
        codes = {s["code"]: s["params"] for s in data["suggestions"]}
        assert codes.get("why_red", codes.get("why_amber")) == {"name": "Aceite Piloto"}
        assert data["summary"]["order_now"] + data["summary"]["order_soon"] >= 1


class TestWhatsAppWiring:

    def test_the_bot_answers_through_the_core_in_spanish_plain_text(self, accounts, fake_llm):
        from backend.whatsapp import agent
        from backend.whatsapp.tools import ToolContext
        a = accounts["a"]
        fake = fake_llm(["**Test**, tu orden sigue pendiente."])
        ctx = ToolContext(tenant_id=a["tid"], user_id=a["uid"], role="admin")
        reply, history, pending = agent.run_turn(ctx, "¿y mi orden?", {"history": [], "pending_action": None})
        assert reply == "*Test*, tu orden sigue pendiente."
        assert pending is None and history[-1]["content"] == reply
        assert "always answer in Spanish" in fake.calls[0]["system"]
        assert "WhatsApp message" in fake.calls[0]["system"]

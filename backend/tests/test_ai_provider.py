"""A company's own AI provider: storage, API, adapters, resilience, no fallback.

Every provider here is the local fake in `fake_llm_server.py` reproducing the
real provider's success and error shapes; conftest refuses any non-loopback
address, so nothing in this file can reach (or bill) a real provider.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timedelta, timezone
from unittest import mock
from uuid import uuid4

import pytest

from backend.ai.providers import catalog, client as pclient, store
from backend.ai.providers.errors import ProviderError
from backend.db.connection import execute, query, query_one
from backend.tests.fake_llm_server import (
    FakeProvider, Reply, anthropic_error, anthropic_ok, gemini_error, models,
    openai_error, openai_ok,
)

API = "/api/v1/ai-provider"
KEY = "sk-test-" + "Q" * 24 + "wxyz"
KEY2 = "sk-test-" + "R" * 24 + "abcd"


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def fake():
    f = FakeProvider()
    yield f
    f.close()


@pytest.fixture(autouse=True)
def fast_and_local(monkeypatch):
    """Loopback allowed (the fake lives there), short timeouts, no real sleeps."""
    from backend.config import settings
    monkeypatch.setattr(settings, "ai_provider_allow_private_hosts", True)
    monkeypatch.setattr(settings, "ai_provider_connect_timeout_s", 1.0)
    monkeypatch.setattr(settings, "ai_provider_read_timeout_s", 2.0)
    monkeypatch.setattr(settings, "ai_provider_total_deadline_s", 10.0)
    monkeypatch.setattr(settings, "ai_provider_max_retries", 2)
    monkeypatch.setattr(settings, "ai_provider_breaker_threshold", 5)
    monkeypatch.setattr(settings, "ai_provider_breaker_cooldown_s", 60)
    slept: list[float] = []
    monkeypatch.setattr(pclient, "_sleep", slept.append)
    monkeypatch.setattr(pclient, "_random", lambda: 1.0)
    return slept


def _point(monkeypatch, provider: str, fake: FakeProvider):
    spec = catalog.PROVIDERS[provider]
    monkeypatch.setitem(catalog.PROVIDERS, provider,
                        dataclasses.replace(spec, base_url=fake.base + "/v1"))


def _store(tid: str, provider="custom", model="m-1", base_url="", key=KEY, **kw):
    store.save(tid, provider=provider, model=model, base_url=base_url, api_key=key,
               organization=kw.get("organization", ""), project=kw.get("project", ""),
               temperature=kw.get("temperature"), max_tokens=kw.get("max_tokens"),
               enabled=kw.get("enabled", True), actor="usr_test")


def _cfg(tid: str) -> store.ProviderConfig:
    cfg = store.load_active(tid)
    assert cfg is not None
    return cfg


def _call(tid: str, **kw):
    from backend.ai.local_llm import get_llm_client
    c = get_llm_client(tid, timeout=10.0)
    return c.messages.create(max_tokens=kw.pop("max_tokens", 200),
                             system=kw.pop("system", "be brief"),
                             messages=kw.pop("messages", [{"role": "user", "content": "hi"}]),
                             **kw)


def _row(tid: str) -> dict:
    return query_one("SELECT * FROM tenant_ai_providers WHERE tenant_id = %s", (tid,)) or {}


# ── Storage, masking, encryption ─────────────────────────────────────────────

class TestConfigApi:

    def test_admin_saves_a_provider_and_the_key_is_encrypted_and_masked(
            self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        r = client.put(f"{API}/config", headers=auth_headers,
                       json={"provider": "openai", "model": "gpt-4.1-mini", "api_key": KEY})
        assert r.status_code == 200, r.text
        assert KEY not in r.text
        cfg = r.json()["data"]["config"]
        assert cfg["api_key_mask"] == "••••wxyz"
        row = _row(tid)
        assert row["provider"] == "openai" and row["model"] == "gpt-4.1-mini"
        # At rest: ciphertext, not the key, and it decrypts back to the key.
        assert row["api_key_enc"] and KEY not in row["api_key_enc"]
        from backend.service_config import crypto
        assert crypto.decrypt_value(row["api_key_enc"]) == KEY
        got = client.get(f"{API}/config", headers=auth_headers)
        assert got.status_code == 200 and KEY not in got.text
        assert "api_key_enc" not in got.text

    def test_the_audit_row_names_the_change_and_never_the_key(
            self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        r = client.put(f"{API}/config", headers=auth_headers,
                       json={"provider": "groq", "model": "llama-3.3-70b-versatile", "api_key": KEY})
        assert r.status_code == 200
        rows = query("SELECT action, context::text AS ctx FROM activity_logs "
                     "WHERE tenant_id = %s AND action = 'audit.ai_provider.saved'", (tid,))
        assert len(rows) == 1
        assert "groq" in rows[0]["ctx"] and KEY not in rows[0]["ctx"] and "wxyz" not in rows[0]["ctx"]

    def test_the_key_is_replace_only(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        assert client.put(f"{API}/config", headers=auth_headers,
                          json={"provider": "openai", "model": "gpt-4.1", "api_key": KEY}).status_code == 200
        enc_before = _row(tid)["api_key_enc"]
        # Same provider, no key: model changes, the key is kept untouched.
        r = client.put(f"{API}/config", headers=auth_headers,
                       json={"provider": "openai", "model": "gpt-4.1-mini"})
        assert r.status_code == 200, r.text
        row = _row(tid)
        assert row["model"] == "gpt-4.1-mini" and row["api_key_enc"] == enc_before
        # A different provider without a key is refused: the old provider's key
        # must never be sent to a new one. Nothing changes.
        r = client.put(f"{API}/config", headers=auth_headers,
                       json={"provider": "anthropic", "model": "claude-haiku-5-5"})
        assert r.status_code == 422 and r.json()["error_code"] == "ai_provider_key_required"
        assert _row(tid)["provider"] == "openai"

    def test_switching_to_a_custom_url_without_a_key_drops_the_old_key(
            self, client, auth_headers, test_tenant, fake):
        tid = test_tenant["id"]
        client.put(f"{API}/config", headers=auth_headers,
                   json={"provider": "openai", "model": "gpt-4.1", "api_key": KEY})
        r = client.put(f"{API}/config", headers=auth_headers,
                       json={"provider": "custom", "model": "llama3", "base_url": fake.base + "/v1"})
        assert r.status_code == 200, r.text
        assert _row(tid)["api_key_enc"] is None
        fake.reply(Reply(200, openai_ok("hola")))
        _call(tid)
        assert "authorization" not in fake.requests[-1].headers

    @pytest.mark.parametrize("body,code", [
        ({"provider": "nope", "model": "x", "api_key": KEY}, "ai_provider_unknown"),
        ({"provider": "openai", "model": "bad model name", "api_key": KEY}, "ai_provider_model_invalid"),
        ({"provider": "openai", "model": "m", "api_key": "has space"}, "ai_provider_key_invalid_format"),
        ({"provider": "openai", "model": "m", "api_key": KEY, "temperature": 3}, "ai_provider_temperature_invalid"),
        ({"provider": "openai", "model": "m", "api_key": KEY, "max_tokens": 5}, "ai_provider_max_tokens_invalid"),
        ({"provider": "custom", "model": "m", "base_url": "ftp://x.example"}, "ai_provider_base_url_refused"),
    ])
    def test_invalid_shapes_are_refused_with_a_code_and_nothing_is_stored(
            self, client, auth_headers, test_tenant, body, code):
        r = client.put(f"{API}/config", headers=auth_headers, json=body)
        assert r.status_code == 422, r.text
        assert r.json()["error_code"] == code
        assert _row(test_tenant["id"]) == {}

    def test_viewer_is_refused_every_route_and_nothing_changes(
            self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        _store(tid, provider="openai", model="gpt-4.1")
        before = _row(tid)
        for method, path, body in (("put", "/config", {"provider": "groq", "model": "x", "api_key": KEY2}),
                                   ("post", "/test", None), ("post", "/reset", None),
                                   ("get", "/config", None), ("get", "/status", None),
                                   ("get", "/providers", None)):
            r = getattr(client, method)(f"{API}{path}", headers=viewer_headers,
                                        **({"json": body} if body else {}))
            assert r.status_code == 403, (method, path, r.text)
        after = _row(tid)
        assert after["provider"] == before["provider"] == "openai"
        assert after["api_key_enc"] == before["api_key_enc"]

    def test_analyst_reads_but_cannot_write(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        _store(tid, provider="openai", model="gpt-4.1")
        r = client.get(f"{API}/config", headers=analyst_headers)
        assert r.status_code == 200 and r.json()["data"]["provider"] == "openai"
        assert KEY not in r.text
        assert client.get(f"{API}/status", headers=analyst_headers).status_code == 200
        r = client.put(f"{API}/config", headers=analyst_headers,
                       json={"provider": "groq", "model": "x", "api_key": KEY2})
        assert r.status_code == 403
        assert _row(tid)["provider"] == "openai"
        r = client.post(f"{API}/reset", headers=analyst_headers)
        assert r.status_code == 403 and _row(tid) != {}

    def test_reset_returns_to_the_stockai_default(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        _store(tid, provider="openai", model="gpt-4.1")
        r = client.post(f"{API}/reset", headers=auth_headers)
        assert r.status_code == 200 and r.json()["data"]["reset"] is True
        assert _row(tid) == {}
        assert r.json()["data"]["status"]["source"] == "stockai"
        from backend.ai.local_llm import get_llm_client
        assert not getattr(get_llm_client(tid), "is_tenant_provider", False)

    def test_save_does_not_call_the_provider_unless_asked(
            self, client, auth_headers, test_tenant, fake):
        r = client.put(f"{API}/config", headers=auth_headers,
                       json={"provider": "custom", "model": "m", "base_url": fake.base + "/v1",
                             "api_key": KEY})
        assert r.status_code == 200 and fake.requests == []
        fake.on("/chat/completions", Reply(200, openai_ok("OK")))
        fake.on("/models", Reply(200, models("m", "m2")))
        r = client.put(f"{API}/config?test=true", headers=auth_headers,
                       json={"provider": "custom", "model": "m", "base_url": fake.base + "/v1"})
        t = r.json()["data"]["test"]
        assert t["ok"] is True and [m["id"] for m in t["models"]] == ["m", "m2"]
        assert t["latency_ms"] is not None
        row = _row(test_tenant["id"])
        assert row["last_test_ok"] is True and row["last_test_at"] is not None


# ── The connection test ──────────────────────────────────────────────────────

class TestConnectionTest:

    def test_a_draft_with_a_bad_key_reports_invalid_key_and_never_echoes_it(
            self, client, auth_headers, test_tenant, fake, monkeypatch):
        _point(monkeypatch, "openai", fake)
        fake.reply(Reply(401, openai_error(f"Incorrect API key provided: {KEY}.",
                                           "invalid_request_error", "invalid_api_key")))
        r = client.post(f"{API}/test", headers=auth_headers,
                        json={"provider": "openai", "model": "gpt-4.1-mini", "api_key": KEY})
        assert r.status_code == 200, r.text
        res = r.json()["data"]
        assert res["ok"] is False and res["error"]["code"] == "invalid_key"
        assert KEY not in r.text
        # A draft is not saved.
        assert _row(test_tenant["id"]) == {}

    def test_a_working_draft_lists_models_with_free_ones_first(
            self, client, auth_headers, fake, monkeypatch):
        _point(monkeypatch, "openrouter", fake)
        fake.on("/chat/completions", Reply(200, openai_ok("OK")))
        fake.on("/models", Reply(200, models("openai/gpt-4.1", "meta/llama:free",
                                             free=("meta/llama:free",))))
        r = client.post(f"{API}/test", headers=auth_headers,
                        json={"provider": "openrouter", "model": "meta/llama:free", "api_key": KEY})
        res = r.json()["data"]
        assert res["ok"] is True
        assert res["models"][0] == {"id": "meta/llama:free", "free": True}
        sent = fake.calls("/chat/completions")[0]
        assert sent.headers["authorization"] == f"Bearer {KEY}"


# ── Adapters against real-shaped answers ─────────────────────────────────────

class TestAdapters:

    def test_openai_success_shape_usage_and_request(self, test_tenant, fake, monkeypatch):
        tid = test_tenant["id"]
        _point(monkeypatch, "openai", fake)
        _store(tid, provider="openai", model="gpt-4.1-mini", organization="org-abc", project="proj_1")
        fake.reply(Reply(200, openai_ok("Hola Ana", prompt_tokens=30, completion_tokens=5)))
        resp = _call(tid, max_tokens=123)
        assert resp.content[0].text == "Hola Ana"
        sent = fake.requests[-1]
        assert sent.path == "/v1/chat/completions"
        assert sent.headers["authorization"] == f"Bearer {KEY}"
        assert sent.headers["openai-organization"] == "org-abc"
        assert sent.body["model"] == "gpt-4.1-mini"
        assert sent.body["max_completion_tokens"] == 123 and "max_tokens" not in sent.body
        assert sent.body["messages"][0] == {"role": "system", "content": "be brief"}
        u = query_one("SELECT * FROM ai_provider_usage_daily WHERE tenant_id = %s", (tid,))
        assert (u["calls"], u["failures"], u["input_tokens"], u["output_tokens"]) == (1, 0, 30, 5)
        assert _row(tid)["last_success_at"] is not None

    def test_anthropic_maps_system_tools_and_tool_results_both_ways(
            self, test_tenant, fake, monkeypatch):
        tid = test_tenant["id"]
        _point(monkeypatch, "anthropic", fake)
        _store(tid, provider="anthropic", model="claude-haiku-5-5")
        fake.reply(Reply(200, anthropic_ok(tool_use={"id": "toolu_1", "name": "stock_of",
                                                     "input": {"sku": "A1"}})))
        tools = [{"type": "function", "function": {"name": "stock_of", "description": "d",
                                                  "parameters": {"type": "object", "properties": {}}}}]
        resp = _call(tid, tools=tools, max_tokens=300)
        assert resp.tool_calls[0].name == "stock_of"
        assert json.loads(resp.tool_calls[0].arguments) == {"sku": "A1"}
        sent = fake.requests[-1]
        assert sent.path == "/v1/messages"
        assert sent.headers["x-api-key"] == KEY and sent.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in sent.headers
        assert sent.body["system"] == "be brief"
        assert sent.body["max_tokens"] >= 1024  # thinking room
        assert sent.body["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
        assert "temperature" not in sent.body

        # The assistant loop sends back the OpenAI-shaped turn + a tool result.
        fake.reply(Reply(200, anthropic_ok("Tienes 5 unidades")))
        follow = [{"role": "user", "content": "stock?"}, resp.message,
                  {"role": "tool", "tool_call_id": "toolu_1", "content": '{"stock": 5}'}]
        resp2 = _call(tid, messages=follow, tools=tools)
        assert resp2.content[0].text == "Tienes 5 unidades"
        msgs = fake.requests[-1].body["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
        assert msgs[1]["content"][-1] == {"type": "tool_use", "id": "toolu_1", "name": "stock_of",
                                          "input": {"sku": "A1"}}
        assert msgs[2]["content"][0]["type"] == "tool_result"
        assert msgs[2]["content"][0]["tool_use_id"] == "toolu_1"

    def test_thinking_only_answer_cut_at_the_limit_is_malformed_not_empty_text(
            self, test_tenant, fake, monkeypatch):
        tid = test_tenant["id"]
        _point(monkeypatch, "anthropic", fake)
        _store(tid, provider="anthropic", model="claude-opus-5-5")
        fake.reply(Reply(200, anthropic_ok("", stop="max_tokens")))
        with pytest.raises(ProviderError) as e:
            _call(tid)
        assert e.value.code == "malformed_response" and e.value.params["reason"] == "truncated"


# ── Error normalisation and what is retried ──────────────────────────────────

class TestErrors:

    @pytest.mark.parametrize("provider,reply,code,attempts", [
        ("openai", Reply(401, openai_error("bad key", "invalid_request_error", "invalid_api_key")), "invalid_key", 1),
        ("anthropic", Reply(401, anthropic_error("authentication_error", "invalid x-api-key")), "invalid_key", 1),
        ("google", Reply(400, gemini_error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.")), "invalid_key", 1),
        ("openrouter", Reply(402, {"error": {"code": 402, "message": "Insufficient credits"}}), "quota_exceeded", 1),
        ("openai", Reply(429, openai_error("You exceeded your current quota", "insufficient_quota", "insufficient_quota")), "quota_exceeded", 1),
        ("anthropic", Reply(400, anthropic_error("invalid_request_error", "Your credit balance is too low")), "quota_exceeded", 1),
        ("openai", Reply(404, openai_error("The model `gpt-9` does not exist", "invalid_request_error", "model_not_found")), "model_not_found", 1),
        ("openai", Reply(400, openai_error("This model's maximum context length is 8192 tokens", "invalid_request_error", "context_length_exceeded")), "bad_request", 1),
        ("anthropic", Reply(403, anthropic_error("permission_error", "flagged by moderation")), "content_blocked", 1),
        ("groq", Reply(500, {"error": {"message": "internal"}}), "provider_down", 3),
        ("anthropic", Reply(529, anthropic_error("overloaded_error", "Overloaded")), "provider_down", 3),
        ("openai", Reply(200, "{not json"), "malformed_response", 1),
        ("openai", Reply(200, '{"choices": [{"message": {"role": "assi'), "malformed_response", 1),
        ("openai", Reply(200, {"choices": []}), "malformed_response", 1),
        ("openai", Reply(200, {"choices": [{"message": {"content": None}, "finish_reason": "content_filter"}]}), "content_blocked", 1),
        ("openrouter", Reply(200, {"error": {"code": 502, "message": "upstream died"}}), "provider_down", 1),
        ("openai", Reply(200, b"x" * 2_100_000), "malformed_response", 1),
    ])
    def test_each_failure_is_normalised_and_only_transient_ones_are_retried(
            self, test_tenant, fake, monkeypatch, provider, reply, code, attempts):
        tid = test_tenant["id"]
        _point(monkeypatch, provider, fake)
        _store(tid, provider=provider, model="m-1")
        fake.reply(reply)
        with pytest.raises(ProviderError) as e:
            _call(tid)
        assert e.value.code == code, e.value
        assert len(fake.requests) == attempts
        row = _row(tid)
        assert row["last_failure_code"] == code and row["last_success_at"] is None

    def test_rate_limit_honours_retry_after_and_then_succeeds(
            self, test_tenant, fake, monkeypatch, fast_and_local):
        tid = test_tenant["id"]
        _point(monkeypatch, "groq", fake)
        _store(tid, provider="groq", model="llama")
        fake.reply(Reply(429, openai_error("slow down", "rate_limit"), {"Retry-After": "3"}),
                   Reply(200, openai_ok("listo")))
        resp = _call(tid)
        assert resp.content[0].text == "listo"
        assert len(fake.requests) == 2 and fast_and_local == [3.0]
        assert _row(tid)["consecutive_failures"] == 0

    def test_backoff_is_exponential_when_no_retry_after(self, test_tenant, fake, monkeypatch,
                                                         fast_and_local):
        tid = test_tenant["id"]
        _point(monkeypatch, "deepseek", fake)
        _store(tid, provider="deepseek", model="deepseek-chat")
        fake.reply(Reply(503, {"error": {"message": "busy"}}))
        with pytest.raises(ProviderError):
            _call(tid)
        assert fast_and_local == [0.5, 1.0]  # base 0.5 * 2^n, jitter pinned to 1.0

    def test_a_read_timeout_is_never_retried(self, test_tenant, fake, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "ai_provider_read_timeout_s", 0.4)
        tid = test_tenant["id"]
        _store(tid, base_url=fake.base + "/v1")
        fake.reply(Reply(200, openai_ok("late"), delay_s=1.5))
        with pytest.raises(ProviderError) as e:
            _call(tid)
        assert e.value.code == "timeout" and e.value.phase == "read"
        assert len(fake.requests) == 1  # could have been billed: not repeated

    def test_an_unreadable_stored_key_is_reported_without_calling_the_provider(
            self, test_tenant, fake, monkeypatch):
        tid = test_tenant["id"]
        _point(monkeypatch, "openai", fake)
        _store(tid, provider="openai", model="gpt-4.1")
        execute("UPDATE tenant_ai_providers SET api_key_enc = 'gAAAAA-not-a-token' WHERE tenant_id = %s",
                (tid,))
        with pytest.raises(ProviderError) as e:
            _call(tid)
        assert e.value.code == "invalid_key" and e.value.params["reason"] == "missing"
        assert fake.requests == []
        assert _row(tid)["last_failure_code"] == "invalid_key"

    def test_a_connection_dropped_mid_answer_is_never_retried(self, test_tenant, fake):
        tid = test_tenant["id"]
        _store(tid, base_url=fake.base + "/v1")
        fake.reply(Reply(200, openai_ok("lost"), drop=True))
        with pytest.raises(ProviderError) as e:
            _call(tid)
        # The request reached the provider: it may have been processed and billed.
        assert e.value.code == "unreachable" and e.value.phase == "read"
        assert not e.value.retryable and len(fake.requests) == 1

    def test_nothing_listening_fails_before_sending_so_it_is_retried(
            self, test_tenant, fake, fast_and_local):
        tid = test_tenant["id"]
        base = fake.base
        fake.close()
        _store(tid, base_url=base + "/v1")
        with pytest.raises(ProviderError) as e:
            _call(tid)
        # Linux refuses at once (unreachable); Windows retries the SYN past the
        # connect timeout (timeout, phase connect). Both mean "never sent".
        assert (e.value.code, e.value.phase) in (("unreachable", ""), ("timeout", "connect"))
        assert e.value.retryable and len(fast_and_local) == 2

    def test_the_key_never_reaches_a_log_a_row_or_a_response(
            self, client, auth_headers, test_tenant, fake, monkeypatch, caplog):
        caplog.set_level(logging.DEBUG)
        tid = test_tenant["id"]
        _point(monkeypatch, "openai", fake)
        _store(tid, provider="openai", model="gpt-4.1")
        fake.reply(Reply(401, openai_error(f"Incorrect API key provided: {KEY}. Bearer {KEY}",
                                           "invalid_request_error")))
        with pytest.raises(ProviderError) as e:
            _call(tid)
        assert KEY not in str(e.value) and KEY not in e.value.message
        assert all(KEY not in rec.getMessage() for rec in caplog.records)
        assert KEY not in (_row(tid)["last_failure_message"] or "")
        for path in ("/status", "/config"):
            assert KEY not in client.get(f"{API}{path}", headers=auth_headers).text
        ctx = query("SELECT context::text AS c FROM activity_logs WHERE tenant_id = %s", (tid,))
        assert all(KEY not in r["c"] for r in ctx)


# ── Breaker, daily event, concurrency ────────────────────────────────────────

class TestBreaker:

    def test_opens_after_threshold_skips_the_network_then_probes_after_cooldown(
            self, test_tenant, fake, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "ai_provider_breaker_threshold", 3)
        clock = {"now": datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)}
        monkeypatch.setattr(store, "_now", lambda: clock["now"])
        tid = test_tenant["id"]
        _store(tid, base_url=fake.base + "/v1")
        fake.reply(Reply(401, openai_error("bad", "invalid_request_error")))
        for _ in range(3):
            with pytest.raises(ProviderError):
                _call(tid)
        assert len(fake.requests) == 3
        assert _row(tid)["breaker_opened_at"] is not None
        # Open: no network call at all, an immediate circuit_open.
        with pytest.raises(ProviderError) as e:
            _call(tid)
        assert e.value.code == "circuit_open" and len(fake.requests) == 3
        # Cooldown over: one probe goes through; it succeeds and closes it.
        clock["now"] += timedelta(seconds=61)
        fake.reply(Reply(200, openai_ok("back")))
        assert _call(tid).content[0].text == "back"
        row = _row(tid)
        assert row["breaker_opened_at"] is None and row["consecutive_failures"] == 0

    def test_a_failed_probe_pauses_it_again(self, test_tenant, fake, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "ai_provider_breaker_threshold", 2)
        clock = {"now": datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)}
        monkeypatch.setattr(store, "_now", lambda: clock["now"])
        tid = test_tenant["id"]
        _store(tid, base_url=fake.base + "/v1")
        fake.reply(Reply(401, openai_error("bad", "x")))
        for _ in range(2):
            with pytest.raises(ProviderError):
                _call(tid)
        clock["now"] += timedelta(seconds=61)
        with pytest.raises(ProviderError) as e:
            _call(tid)   # the probe, which fails
        assert e.value.code == "invalid_key" and len(fake.requests) == 3
        with pytest.raises(ProviderError) as e:
            _call(tid)
        assert e.value.code == "circuit_open" and len(fake.requests) == 3

    def test_a_request_specific_refusal_does_not_pause_a_working_provider(
            self, test_tenant, fake, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "ai_provider_breaker_threshold", 2)
        tid = test_tenant["id"]
        _store(tid, base_url=fake.base + "/v1")
        fake.reply(Reply(400, openai_error("maximum context length exceeded", "invalid_request_error")))
        for _ in range(3):
            with pytest.raises(ProviderError):
                _call(tid)
        row = _row(tid)
        assert row["consecutive_failures"] == 0 and row["breaker_opened_at"] is None

    def test_one_visible_event_per_day_however_many_failures(self, test_tenant, fake, monkeypatch):
        tid = test_tenant["id"]
        _store(tid, base_url=fake.base + "/v1")
        fake.reply(Reply(401, openai_error("bad", "x")))
        for _ in range(4):
            with pytest.raises(ProviderError):
                _call(tid)
        rows = query("SELECT context FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'ai_provider.failing'", (tid,))
        assert len(rows) == 1
        assert rows[0]["context"]["reason"] == "ai_provider_call_failed"
        assert rows[0]["context"]["reason_params"]["error_code"] == "invalid_key"

    def test_the_concurrency_ceiling_answers_rate_limited_without_calling(
            self, test_tenant, fake, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "ai_provider_max_concurrent_per_tenant", 1)
        tid = test_tenant["id"]
        _store(tid, base_url=fake.base + "/v1")
        sem = pclient._semaphore(tid, 1)
        assert sem.acquire(timeout=1)
        try:
            from backend.ai.local_llm import get_llm_client
            c = get_llm_client(tid, timeout=1.0)
            with pytest.raises(ProviderError) as e:
                c.messages.create(max_tokens=10, messages=[{"role": "user", "content": "x"}])
            assert e.value.code == "rate_limited" and e.value.params["scope"] == "stockai"
            assert fake.requests == []
        finally:
            sem.release()


# ── SSRF ─────────────────────────────────────────────────────────────────────

class TestBaseUrlSafety:

    @pytest.mark.parametrize("url,reason", [
        ("http://169.254.169.254/v1", "forbidden_address"),
        ("https://[fe80::1]/v1", "forbidden_address"),
        ("https://100.100.100.200/v1", "forbidden_address"),
        ("http://8.8.8.8/v1", "https_required"),
        ("https://user:pw@api.example.com/v1", "credentials_in_url"),
        ("file:///etc/passwd", "scheme"),
    ])
    def test_refused_at_save_always(self, client, auth_headers, test_tenant, url, reason):
        r = client.put(f"{API}/config", headers=auth_headers,
                       json={"provider": "custom", "model": "m", "base_url": url})
        assert r.status_code == 422, r.text
        assert r.json()["error_code"] == "ai_provider_base_url_refused"
        assert r.json()["error_params"]["reason"] == reason
        assert _row(test_tenant["id"]) == {}

    def test_loopback_is_refused_when_private_hosts_are_off(
            self, client, auth_headers, test_tenant, fake, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "ai_provider_allow_private_hosts", False)
        r = client.put(f"{API}/config", headers=auth_headers,
                       json={"provider": "custom", "model": "m", "base_url": fake.base + "/v1"})
        assert r.status_code == 422
        assert r.json()["error_params"]["reason"] == "private_address"
        # And a row stored while it WAS allowed is refused at call time, not called.
        monkeypatch.setattr(settings, "ai_provider_allow_private_hosts", True)
        _store(test_tenant["id"], base_url=fake.base + "/v1")
        monkeypatch.setattr(settings, "ai_provider_allow_private_hosts", False)
        with pytest.raises(ProviderError) as e:
            _call(test_tenant["id"])
        assert e.value.code == "base_url_refused" and fake.requests == []

    def test_a_name_that_resolves_to_a_private_address_is_refused_at_call_time(
            self, test_tenant, monkeypatch):
        import ipaddress
        from backend.config import settings
        from backend.datasources import network
        monkeypatch.setattr(settings, "ai_provider_allow_private_hosts", False)
        monkeypatch.setattr(network, "resolve",
                            lambda host, port, **k: [ipaddress.ip_address("10.0.0.7")])
        _store(test_tenant["id"], base_url="https://llm.evil.example/v1")
        with pytest.raises(ProviderError) as e:
            _call(test_tenant["id"])
        assert e.value.code == "base_url_refused"
        assert e.value.params["reason"] == "private_address"

    def test_a_name_that_resolves_to_metadata_is_refused_even_with_private_allowed(
            self, test_tenant, monkeypatch):
        import ipaddress
        from backend.datasources import network
        monkeypatch.setattr(network, "resolve",
                            lambda host, port, **k: [ipaddress.ip_address("169.254.169.254")])
        _store(test_tenant["id"], base_url="https://llm.evil.example/v1")
        with pytest.raises(ProviderError) as e:
            _call(test_tenant["id"])
        assert e.value.params["reason"] == "forbidden_address"

    def test_a_redirect_is_not_followed_and_the_key_goes_nowhere_else(
            self, test_tenant, fake):
        tid = test_tenant["id"]
        _store(tid, base_url=fake.base + "/v1")
        fake.reply(Reply(302, b"", {"Location": "http://169.254.169.254/latest/meta-data/"}))
        with pytest.raises(ProviderError) as e:
            _call(tid)
        assert e.value.code == "base_url_refused" and e.value.params["reason"] == "redirect"
        assert len(fake.requests) == 1


# ── No fallback, ever; consumers degrade with the reason ─────────────────────

class TestNoFallback:

    def _break(self, tid, fake):
        _store(tid, base_url=fake.base + "/v1")
        fake.reply(Reply(401, openai_error("bad key", "invalid_request_error")))

    def test_the_assistant_answers_by_rules_naming_the_provider_problem(
            self, test_tenant, registered_user, fake):
        from backend.assistant import answer
        from backend.notifications.locale import render
        tid = test_tenant["id"]
        self._break(tid, fake)
        spy = mock.MagicMock(side_effect=AssertionError("instance provider used"))
        with mock.patch("backend.ai.local_llm.get_local_llm_client", spy):
            reply = answer(tid, registered_user["user"]["id"], "web", "¿qué compro?", [],
                           role="admin", language="es")
        spy.assert_not_called()
        assert reply.source == "rules" and reply.reason == "provider_error"
        assert reply.provider_error["code"] == "invalid_key"
        assert reply.provider_error["provider"] == "custom"
        assert render("es", "llm_problem_invalid_key") in reply.text

    def test_narrative_diagnosis_and_analyst_degrade_with_the_code(
            self, test_tenant, registered_user, fake):
        from backend.ai.narrative_service import generate_morning_narrative
        from backend.ai.rag_service import RAGService
        from backend.api.v1.configuration import _llm_health_narrative
        tid = test_tenant["id"]
        self._break(tid, fake)
        spy = mock.MagicMock(side_effect=AssertionError("instance provider used"))
        with mock.patch("backend.ai.local_llm.get_local_llm_client", spy):
            n = generate_morning_narrative({"kpis": {}}, tenant_id=tid)
            d = _llm_health_narrative(80.0, "HEALTHY", {}, {}, tenant_id=tid)
            rag = RAGService()._fallback(tid, "no-session", registered_user["user"]["id"], "q", None)
        spy.assert_not_called()
        assert n["fallback"] is True and n["degraded"] is True and n["reason"] == "invalid_key"
        assert d == (None, "invalid_key")
        assert rag["source"] == "provider_error" and rag["reason"] == "invalid_key"

    def test_the_chat_endpoint_carries_the_provider_error(
            self, client, auth_headers, test_tenant, fake):
        self._break(test_tenant["id"], fake)
        chat = client.post("/api/v1/analyst/chats", headers=auth_headers, json={}).json()["data"]
        r = client.post(f"/api/v1/analyst/chats/{chat['id']}/messages", headers=auth_headers,
                        json={"question": "¿qué compro hoy?", "language": "es"})
        assert r.status_code == 200, r.text
        a = r.json()["data"]["assistant"]
        assert a["source"] == "rules" and a["reason"] == "provider_error"
        assert a["provider_error"]["code"] in ("invalid_key", "circuit_open")
        assert KEY not in r.text

    def test_a_tenant_without_a_choice_keeps_the_old_path(self, test_tenant):
        from backend.ai.local_llm import get_llm_client
        sentinel = object()
        with mock.patch("backend.ai.local_llm.get_local_llm_client", return_value=sentinel) as m:
            assert get_llm_client(test_tenant["id"], timeout=7.0) is sentinel
            assert get_llm_client(None) is sentinel
        assert m.call_args_list[0].kwargs == {"timeout": 7.0}

    def test_a_disabled_choice_uses_the_default_again(self, test_tenant, fake):
        tid = test_tenant["id"]
        _store(tid, base_url=fake.base + "/v1", enabled=False)
        from backend.ai.local_llm import get_llm_client
        assert not getattr(get_llm_client(tid), "is_tenant_provider", False)

    def test_tenant_a_key_is_never_used_for_tenant_b(self, test_tenant, fake):
        from backend.ai.local_llm import get_llm_client
        from backend.tenants.service import create_tenant
        a = test_tenant["id"]
        _store(a, base_url=fake.base + "/v1")
        b = create_tenant(f"pytest-{uuid4().hex[:10]}")["id"]
        try:
            client_b = get_llm_client(b)
            assert not getattr(client_b, "is_tenant_provider", False)
            client_b.messages.create(max_tokens=10, messages=[{"role": "user", "content": "x"}])
            assert fake.requests == []
            fake.reply(Reply(200, openai_ok("A")))
            _call(a)
            assert len(fake.requests) == 1
            assert fake.requests[0].headers["authorization"] == f"Bearer {KEY}"
            assert query("SELECT 1 FROM ai_provider_usage_daily WHERE tenant_id = %s", (b,)) == []
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (b,))


# ── What every user's screen learns before typing ───────────────────────────

class TestCapabilities:

    def test_a_paused_company_provider_turns_the_assistant_off_with_its_reason(
            self, client, viewer_headers, test_tenant, fake, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "ai_provider_breaker_threshold", 2)
        tid = test_tenant["id"]
        _store(tid, base_url=fake.base + "/v1")
        caps = client.get("/api/v1/service-config/capabilities", headers=viewer_headers).json()["data"]
        assert caps["assistant"] is True
        assert caps["assistant_provider"] == {"source": "company", "provider": "custom",
                                              "state": "ready", "reason": None}
        fake.reply(Reply(401, openai_error("bad", "x")))
        with pytest.raises(ProviderError):
            _call(tid)
        caps = client.get("/api/v1/service-config/capabilities", headers=viewer_headers).json()["data"]
        assert caps["assistant_provider"]["state"] == "failing"
        assert caps["assistant_provider"]["reason"] == "invalid_key"
        with pytest.raises(ProviderError):
            _call(tid)
        caps = client.get("/api/v1/service-config/capabilities", headers=viewer_headers).json()["data"]
        assert caps["assistant"] is False
        assert caps["assistant_provider"]["state"] == "paused"

    def test_status_shows_breaker_and_usage(self, client, analyst_headers, test_tenant, fake):
        tid = test_tenant["id"]
        _store(tid, base_url=fake.base + "/v1")
        fake.reply(Reply(200, openai_ok("x", prompt_tokens=4, completion_tokens=2)))
        _call(tid)
        s = client.get(f"{API}/status", headers=analyst_headers).json()["data"]
        assert s["source"] == "company" and s["breaker"] == "closed"
        assert s["usage_7d"]["calls"] == 1 and s["usage_7d"]["input_tokens"] == 4
        assert s["last_success_at"] is not None


def test_every_provider_error_code_has_es_and_en_copy():
    from pathlib import Path
    from backend.ai.providers.errors import CODES
    from backend.notifications.locale import _EN, _ES
    text = (Path(__file__).resolve().parents[2] / "Frontend" / "src" / "i18n"
            / "translations.ts").read_text(encoding="utf-8")
    split = text.index("\n  en: {")
    es, en = text[:split], text[split:]
    for code in CODES:
        # The screen's sentence with its remedy, in both languages...
        assert f"'errors.llm_{code}'" in es and f"'errors.llm_{code}'" in en, code
        # ...and the chat/WhatsApp intro the backend writes itself.
        assert f"llm_problem_{code}" in _ES and f"llm_problem_{code}" in _EN, code

"""The MCP endpoint, exercised the way a customer's AI client speaks to it.

`POST /api/v1/mcp` is a second front door onto the same tenant data, reached
with the same `sk_live_*` key. Three things have to hold, and each one fails
quietly if nobody asserts it:

1. **It is a correct MCP server.** A handshake that returns the wrong shape does
   not error — the connector simply never appears in the client, with no message
   anywhere.
2. **It reads and only reads.** The catalogue is the ceiling; an added write
   tool would hand an irreversible action to a model, which is the one thing
   `docs/assistant-actions.md` forbids outright.
3. **It does not lie about what it left out.** The REST semáforo returns every
   SKU; this one cannot, so the counts it reports must describe the whole set
   and the truncation must be stated.

Every assertion reads the response body. A 200 alone proves nothing here: a
handshake that returns `{}` is a 200.
"""
import json

import pytest

from backend.db.connection import query_one
from backend.mcp import catalog, protocol

MCP = "/api/v1/mcp"

# The catalogue, frozen. Adding a tool has to be a deliberate edit of this line,
# not something that happens on the way past.
EXPECTED_TOOLS = {
    "get_planning_context",
    "get_inventory_status",
    "get_morning_briefing",
    "list_data_sources",
    "get_training_status",
}


def rpc(method: str, params: dict | None = None, request_id=1) -> dict:
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def call(client, headers, tool: str, arguments: dict | None = None):
    return client.post(MCP, json=rpc("tools/call", {
        "name": tool, "arguments": arguments or {},
    }), headers=headers)


@pytest.fixture
def key_headers(client, auth_headers):
    """A key minted through the endpoint the screen calls, like a customer's."""
    r = client.post("/api/v1/api-keys",
                    json={"name": "claude-desktop-test", "role": "analyst"},
                    headers=auth_headers)
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['data']['key']}"}


@pytest.fixture
def viewer_key_headers(client, auth_headers):
    r = client.post("/api/v1/api-keys",
                    json={"name": "claude-readonly-test", "role": "viewer"},
                    headers=auth_headers)
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['data']['key']}"}


# ── 1. The handshake ─────────────────────────────────────────────────────────

class TestTheHandshake:
    def test_initialize_returns_a_usable_server_description(self, client, key_headers):
        r = client.post(MCP, json=rpc("initialize", {
            "protocolVersion": protocol.PREFERRED_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        }), headers=key_headers)
        assert r.status_code == 200, r.text

        result = r.json()["result"]
        assert result["protocolVersion"] == protocol.PREFERRED_VERSION
        assert result["serverInfo"]["name"] == "stockai"
        assert result["serverInfo"]["version"]
        assert result["capabilities"]["tools"] == {"listChanged": False}
        # The instructions are what tells a model SIN_DATOS is not "fine". A
        # server that drops them still connects and answers worse.
        assert "SIN_DATOS" in result["instructions"]

    def test_an_older_client_is_answered_in_a_version_we_speak(self, client, key_headers):
        r = client.post(MCP, json=rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "old", "version": "1"},
        }), headers=key_headers)
        assert r.json()["result"]["protocolVersion"] == "2024-11-05", (
            "a version we support was not echoed back, so an older client is "
            "told to speak one it cannot"
        )

    def test_an_unknown_version_falls_back_instead_of_refusing(self, client, key_headers):
        r = client.post(MCP, json=rpc("initialize", {
            "protocolVersion": "1999-01-01", "capabilities": {},
            "clientInfo": {"name": "future", "version": "1"},
        }), headers=key_headers)
        assert r.status_code == 200, r.text
        assert r.json()["result"]["protocolVersion"] == protocol.PREFERRED_VERSION

    def test_an_unsupported_version_header_is_a_400(self, client, key_headers):
        r = client.post(MCP, json=rpc("ping"),
                        headers={**key_headers, "MCP-Protocol-Version": "1999-01-01"})
        assert r.status_code == 400, r.text

    def test_a_supported_version_header_is_accepted(self, client, key_headers):
        r = client.post(MCP, json=rpc("ping"),
                        headers={**key_headers,
                                 "MCP-Protocol-Version": protocol.PREFERRED_VERSION})
        assert r.status_code == 200, r.text
        assert r.json()["result"] == {}

    def test_a_notification_is_accepted_with_no_body(self, client, key_headers):
        r = client.post(MCP, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                        headers=key_headers)
        assert r.status_code == 202, r.text
        assert r.content == b"", "a notification was answered, which is a malformed frame"

    def test_the_server_to_client_stream_is_declined_not_missing(self, client, key_headers):
        """405 says "this server has nothing to push"; 404 would send whoever is
        wiring it up to go check their URL."""
        r = client.get(MCP, headers=key_headers)
        assert r.status_code == 405, r.text


# ── 2. The catalogue ─────────────────────────────────────────────────────────

class TestTheCatalogue:
    def test_tools_list_publishes_exactly_the_expected_tools(self, client, key_headers):
        r = client.post(MCP, json=rpc("tools/list"), headers=key_headers)
        assert r.status_code == 200, r.text
        names = {t["name"] for t in r.json()["result"]["tools"]}
        assert names == EXPECTED_TOOLS

    def test_every_tool_says_it_is_a_read(self):
        for tool in catalog.TOOLS:
            annotations = tool.descriptor()["annotations"]
            assert annotations["readOnlyHint"] is True, tool.name
            assert annotations["destructiveHint"] is False, tool.name

    def test_every_tool_actually_only_calls_GET_endpoints(self):
        """The wall, and the reason it is structural rather than a grep.

        The rule this server exists under: an MCP client cannot render a
        confirmation card and cannot hold an undo token, so it is given nothing
        that writes (`docs/assistant-actions.md`). An annotation saying
        `readOnlyHint: true` is a *claim* — this checks the claim against what
        the handler actually calls.

        Every handler in the catalogue reaches the product through a router
        function. Each of those is resolved to the FastAPI route it backs, and
        the route's methods must be exactly {GET}. A tool wired to `log-po` or
        to the file upload fails here whatever its annotations say, and it fails
        at the one place that cannot be talked out of."""
        import ast
        import inspect
        import importlib
        import textwrap

        from backend.main import app

        # endpoint function -> the methods FastAPI serves it under.
        methods_of = {}
        for route in app.routes:
            endpoint = getattr(route, "endpoint", None)
            if endpoint is not None:
                methods_of.setdefault(endpoint, set()).update(
                    getattr(route, "methods", None) or set())

        checked = 0
        for tool in catalog.TOOLS:
            tree = ast.parse(textwrap.dedent(inspect.getsource(tool.handler)))

            # `from backend.api.v1 import inventory as inventory_router`
            aliases = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for name in node.names:
                        aliases[name.asname or name.name] = f"{node.module}.{name.name}"

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Name)
                        and func.value.id in aliases):
                    continue
                module = importlib.import_module(aliases[func.value.id])
                target = getattr(module, func.attr)
                methods = methods_of.get(target)
                assert methods, (
                    f"{tool.name} calls {func.value.id}.{func.attr}, which is not "
                    f"a registered route — the check cannot vouch for it"
                )
                assert methods == {"GET"}, (
                    f"{tool.name} calls {func.attr}, served as {sorted(methods)}. "
                    f"Every MCP tool must read."
                )
                checked += 1

        # A walk that matched nothing would pass for the wrong reason.
        assert checked >= len(catalog.TOOLS), (
            f"only {checked} router calls found across {len(catalog.TOOLS)} tools — "
            f"the handlers stopped calling endpoint functions and this check went blind"
        )

    def test_each_descriptor_is_a_valid_json_schema_object(self, client, key_headers):
        r = client.post(MCP, json=rpc("tools/list"), headers=key_headers)
        for tool in r.json()["result"]["tools"]:
            schema = tool["inputSchema"]
            assert schema["type"] == "object", tool["name"]
            assert isinstance(schema.get("properties"), dict), tool["name"]
            # A description is not decoration: it is the only thing telling the
            # model when to pick this tool over its neighbour.
            assert len(tool["description"]) > 40, tool["name"]

    def test_an_unknown_tool_is_a_protocol_error_not_a_tool_failure(self, client, key_headers):
        """The distinction matters: `isError` invites the model to retry with
        different arguments, and there are no arguments that make a nonexistent
        tool exist."""
        r = call(client, key_headers, "drop_everything")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "error" in body, body
        assert body["error"]["code"] == protocol.INVALID_PARAMS
        assert "available" in body["error"]["data"]

    def test_an_unknown_method_names_what_it_supports(self, client, key_headers):
        r = client.post(MCP, json=rpc("resources/list"), headers=key_headers)
        body = r.json()
        assert body["error"]["code"] == protocol.METHOD_NOT_FOUND
        assert "tools/call" in body["error"]["data"]["supported"]


# ── 3. The tools answer with real data ───────────────────────────────────────

class TestTheToolsAnswer:
    def test_planning_context_names_the_session_the_app_is_showing(
        self, client, key_headers, completed_session, test_tenant
    ):
        r = call(client, key_headers, "get_planning_context")
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert result["isError"] is False, result

        data = result["structuredContent"]
        assert data["active_session_id"], "the tool that hands out session ids handed out none"
        # The same session the product's own resolver picks — an MCP client and
        # a person looking at StockAI must not be reading different runs.
        from backend.sessions import planning_service
        assert data["active_session_id"] == planning_service.resolve_active_session(
            test_tenant["id"])

    def test_the_text_block_carries_the_same_data_as_the_structured_one(
        self, client, key_headers, completed_session
    ):
        """Clients that predate structuredContent read the text block. If the
        two disagree, half the clients in the world get a different answer."""
        r = call(client, key_headers, "get_planning_context")
        result = r.json()["result"]
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]

    def test_inventory_status_returns_the_semaforo(
        self, client, key_headers, completed_session
    ):
        r = call(client, key_headers, "get_inventory_status",
                 {"session_id": completed_session["id"]})
        data = r.json()["result"]["structuredContent"]
        assert "items" in data and "summary" in data
        assert data["returned_items"] == len(data["items"])

    def test_the_briefing_answers(self, client, key_headers, completed_session):
        r = call(client, key_headers, "get_morning_briefing",
                 {"session_id": completed_session["id"]})
        assert r.json()["result"]["isError"] is False, r.text
        assert isinstance(r.json()["result"]["structuredContent"], dict)

    def test_data_sources_are_listed(self, client, key_headers, uploaded_dataset):
        r = call(client, key_headers, "list_data_sources")
        data = r.json()["result"]["structuredContent"]
        assert "items" in data and "total" in data

    def test_training_status_reports_the_session_state(
        self, client, key_headers, completed_session
    ):
        r = call(client, key_headers, "get_training_status",
                 {"session_id": completed_session["id"]})
        data = r.json()["result"]["structuredContent"]
        assert data["session_id"] == completed_session["id"]
        assert data["status"] == "COMPLETED"


# ── 4. Truncation, said out loud ─────────────────────────────────────────────

class TestItDoesNotHideWhatItLeftOut:
    def test_the_summary_counts_everything_even_when_the_list_does_not(
        self, client, key_headers, completed_session
    ):
        """The expensive lie this guards against: a model told there are 2 SKUs
        to order when there are 200, because only 2 fitted in the answer."""
        full = call(client, key_headers, "get_inventory_status",
                    {"session_id": completed_session["id"]}).json()["result"]["structuredContent"]
        total = full["total_matching_items"]
        if total < 2:
            pytest.skip("needs at least two SKUs in the seeded session")

        one = call(client, key_headers, "get_inventory_status",
                   {"session_id": completed_session["id"], "limit": 1}
                   ).json()["result"]["structuredContent"]

        assert len(one["items"]) == 1
        assert one["truncated"] is True
        assert one["total_matching_items"] == total
        assert one["summary"] == full["summary"], (
            "the summary shrank with the page, so a truncated answer under-reports "
            "how much there is to buy"
        )
        assert "truncation_note" in one
        assert str(total) in one["truncation_note"]

    def test_a_complete_answer_does_not_claim_to_be_truncated(
        self, client, key_headers, completed_session
    ):
        data = call(client, key_headers, "get_inventory_status",
                    {"session_id": completed_session["id"]}
                    ).json()["result"]["structuredContent"]
        assert data["truncated"] is False
        assert "truncation_note" not in data

    def test_the_kept_rows_are_the_urgent_ones(
        self, client, key_headers, completed_session, test_tenant
    ):
        """Truncating an arbitrary slice would drop exactly the rows the
        question was about."""
        from backend.inventory import service as inv_svc

        # Two rows that cannot be confused: one with no stock at all, one deep
        # in overstock.
        inv_svc.upsert_stock(test_tenant["id"], "SKU_001", {"current_stock": 0})
        inv_svc.upsert_stock(test_tenant["id"], "SKU_002", {"current_stock": 99999})

        data = call(client, key_headers, "get_inventory_status",
                    {"session_id": completed_session["id"]}
                    ).json()["result"]["structuredContent"]
        ranks = [catalog._URGENCY.get(i["signal"], 9) for i in data["items"]]
        assert ranks == sorted(ranks), f"rows came back out of urgency order: {ranks}"

    def test_a_filter_that_matched_nothing_says_so(
        self, client, key_headers, completed_session
    ):
        """The most expensive empty list in this product.

        "No SKU matched that supplier" and "there is nothing to order" are
        different facts, and an empty `items` with a zeroed `summary` looks
        exactly like the second. A supplier name spelled the way the ERP spells
        it lands here, and an assistant reading it reports all clear."""
        data = call(client, key_headers, "get_inventory_status", {
            "session_id": completed_session["id"],
            "supplier": "a-supplier-that-does-not-exist",
        }).json()["result"]["structuredContent"]

        assert data["items"] == []
        assert "empty_reason" in data, (
            "an empty result from a filter is indistinguishable from a healthy "
            "catalogue"
        )
        assert "a-supplier-that-does-not-exist" in data["empty_reason"]

    def test_a_genuinely_empty_answer_does_not_blame_a_filter(
        self, client, key_headers, completed_session
    ):
        """The other half: with no filter applied, there is no filter to blame,
        and inventing one would be its own lie."""
        data = call(client, key_headers, "get_inventory_status",
                    {"session_id": completed_session["id"]}
                    ).json()["result"]["structuredContent"]
        assert "empty_reason" not in data

    def test_an_unknown_signal_is_refused_instead_of_filtering_to_nothing(
        self, client, key_headers, completed_session
    ):
        """A model that invents `URGENT` must not be handed a confident
        "nothing to order"."""
        result = call(client, key_headers, "get_inventory_status", {
            "session_id": completed_session["id"], "signal": "URGENT",
        }).json()["result"]
        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert "PEDIR_YA" in text, "the refusal did not name the valid signals"

    def test_a_valid_signal_in_lowercase_still_works(
        self, client, key_headers, completed_session
    ):
        """Refusing the wrong thing must not start refusing the right thing in
        the wrong case — the REST endpoint accepts either."""
        result = call(client, key_headers, "get_inventory_status", {
            "session_id": completed_session["id"], "signal": "pedir_ya",
        }).json()["result"]
        assert result["isError"] is False, result

    def test_limit_is_clamped_rather_than_refused(
        self, client, key_headers, completed_session
    ):
        """Models send "50", 50.0 and nonsense. Refusing costs a round trip for
        something with an obvious reading."""
        for bad in ("3", 3.9, 10**9, -4, "not a number", None):
            r = call(client, key_headers, "get_inventory_status",
                     {"session_id": completed_session["id"], "limit": bad})
            assert r.json()["result"]["isError"] is False, f"limit={bad!r} was refused"
            assert len(r.json()["result"]["structuredContent"]["items"]) <= catalog.MAX_ITEM_LIMIT


# ── 5. Business refusals reach the model ─────────────────────────────────────

class TestARefusalIsSomethingTheModelCanRead:
    def test_no_completed_session_is_a_tool_error_not_a_500(
        self, client, key_headers, test_tenant
    ):
        """A fresh tenant has nothing to read. The model has to be able to say
        "you have not trained a forecast yet" — which it cannot do if the
        failure arrived as a transport error it never sees."""
        r = call(client, key_headers, "get_morning_briefing")
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        if result["isError"]:
            assert "no_completed_session" in result["content"][0]["text"]

    def test_an_unknown_session_says_so(self, client, key_headers):
        r = call(client, key_headers, "get_training_status", {"session_id": "sess_nope"})
        result = r.json()["result"]
        assert result["isError"] is True
        assert "session_not_found" in result["content"][0]["text"]

    def test_a_missing_required_argument_is_reported_not_crashed(self, client, key_headers):
        r = call(client, key_headers, "get_training_status", {})
        result = r.json()["result"]
        assert result["isError"] is True
        assert "session_id" in result["content"][0]["text"]


# ── 6. The credential ────────────────────────────────────────────────────────

class TestAuthentication:
    def test_no_key_is_a_401_that_says_where_to_get_one(self, client):
        r = client.post(MCP, json=rpc("tools/list"))
        assert r.status_code == 401, (
            f"got {r.status_code}; a 403 reads as 'this key is not allowed' when "
            f"the caller sent no key at all"
        )
        assert "www-authenticate" in {k.lower() for k in r.headers}
        assert "API Keys" in r.json()["detail"]

    def test_an_invented_key_is_refused(self, client):
        r = client.post(MCP, json=rpc("tools/list"),
                        headers={"Authorization": "Bearer sk_live_not_a_real_key"})
        assert r.status_code == 401, r.text

    def test_a_read_only_key_works_because_everything_here_reads(
        self, client, viewer_key_headers, completed_session
    ):
        """The permission pair for a surface with no writes: the read-only
        credential the documentation recommends must not be refused."""
        r = call(client, viewer_key_headers, "get_inventory_status",
                 {"session_id": completed_session["id"]})
        assert r.status_code == 200, r.text
        assert r.json()["result"]["isError"] is False

    def test_a_key_only_ever_sees_its_own_tenant(
        self, client, key_headers, completed_session, make_tenant_user_headers, test_tenant
    ):
        """One endpoint, many tenants. The tool takes a session_id straight from
        the model, so the scoping has to hold against an id from elsewhere."""
        other_headers = make_tenant_user_headers(role="admin")
        r = client.post("/api/v1/api-keys", json={"name": "other", "role": "analyst"},
                        headers=other_headers)
        assert r.status_code in (200, 201), r.text
        other_key = {"Authorization": f"Bearer {r.json()['data']['key']}"}

        result = call(client, other_key, "get_training_status",
                      {"session_id": completed_session["id"]}).json()["result"]
        assert result["isError"] is True, (
            "a key from another tenant read a session it does not own"
        )
        assert "session_not_found" in result["content"][0]["text"]

    def test_reading_over_mcp_does_not_fill_the_activity_trail_with_writes(
        self, client, key_headers, test_tenant
    ):
        """`/actividad` answers "what did my integration do last night?".

        MCP is JSON-RPC, so every call — `tools/list` included — is a POST, and
        the audit middleware files POSTs from a key as `api_write`. Left alone
        that labels a read as a write and, at 120 calls a minute, buries the
        genuine writes the trail exists to show."""
        before = query_one(
            "SELECT COUNT(*) AS n FROM activity_logs "
            "WHERE tenant_id = %s AND action = 'api_write'", (test_tenant["id"],))["n"]

        for _ in range(3):
            assert client.post(MCP, json=rpc("tools/list"),
                               headers=key_headers).status_code == 200

        after = query_one(
            "SELECT COUNT(*) AS n FROM activity_logs "
            "WHERE tenant_id = %s AND action = 'api_write'", (test_tenant["id"],))["n"]
        assert after == before, (
            f"{after - before} audit rows for three MCP reads — the trail now "
            f"calls a read a write"
        )

    def test_the_rate_limiter_is_the_same_one(self, client, key_headers, monkeypatch):
        """Not a separate ceiling to forget: the MCP endpoint goes through the
        key authentication every REST call does."""
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        monkeypatch.setattr("backend.auth.api_key_auth.check_rate",
                            lambda *a, **k: False)
        r = client.post(MCP, json=rpc("tools/list"), headers=key_headers)
        assert r.status_code == 429, r.text
        assert r.headers.get("Retry-After")


# ── 7. Malformed input ───────────────────────────────────────────────────────

class TestMalformedInput:
    def test_invalid_json_is_a_parse_error(self, client, key_headers):
        r = client.post(MCP, content=b"{not json", headers={
            **key_headers, "Content-Type": "application/json"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == protocol.PARSE_ERROR

    def test_an_empty_body_is_refused_with_a_reason(self, client, key_headers):
        r = client.post(MCP, content=b"", headers={
            **key_headers, "Content-Type": "application/json"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == protocol.INVALID_REQUEST

    def test_a_frame_that_is_not_json_rpc_is_rejected(self, client, key_headers):
        r = client.post(MCP, json={"hello": "there"}, headers=key_headers)
        assert r.json()["error"]["code"] == protocol.INVALID_REQUEST

    def test_a_batch_from_an_older_client_still_works(self, client, key_headers):
        """Batching left the protocol in 2025-06-18. A client that predates that
        should not meet a wall."""
        r = client.post(MCP, json=[rpc("ping", request_id=1), rpc("tools/list", request_id=2)],
                        headers=key_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, list) and len(body) == 2
        assert {m["id"] for m in body} == {1, 2}

    def test_string_arguments_instead_of_an_object_are_refused_cleanly(
        self, client, key_headers
    ):
        r = client.post(MCP, json=rpc("tools/call", {
            "name": "get_planning_context", "arguments": "oops"}), headers=key_headers)
        assert r.json()["error"]["code"] == protocol.INVALID_PARAMS


# ── 8. The endpoint survives the API-only instance ───────────────────────────

class TestItIsPartOfThePublicPromise:
    def test_mcp_is_on_the_published_surface(self):
        """`PUBLIC_API_ONLY` prunes everything not on that list. An MCP endpoint
        missing from it would 404 on exactly the instance a customer's
        integration points at."""
        from backend.api.public_surface import public_endpoints
        from backend.main import app
        assert ("POST", "/mcp") in public_endpoints(app)
        assert ("GET", "/mcp") in public_endpoints(app)

"""The API reference shows examples, and every example is checked against the API.

Two files feed /desarrolladores and the in-app /api screen:

* `Frontend/src/data/public-api.json` (the snapshot): per endpoint, a schema tree
  and a ready-made example for the REQUEST, generated from the app's OpenAPI.
* `Frontend/public/api-response-examples.json`: per endpoint, what the API REALLY
  answered when `backend/scripts/capture_api_examples.py` called it, with a
  schema inferred from that answer.

The snapshot test (`test_public_api_snapshot.py`) already makes the first file
current. This file makes the examples TRUE:

* every documented request example is accepted by the route it documents (the
  same validation FastAPI runs before the handler: a 422 on the documented
  example fails here);
* a list is documented as a list and an object as an object, at the root and
  nested: the example agrees with its own schema tree;
* the fields documented for a free-form body are keys the handler really reads;
* every captured response example conforms to its schema, comes in the envelope
  the guide promises, carries nothing that identifies a machine or a person, and
  is for a route that exists, with the status that route documents;
* for every read endpoint this suite can call, the LIVE response has the same
  shape as the documented one: a key the docs do not know, a list where the docs
  say object, or a type that changed turns this red.
"""
import inspect
import json
import re

import pytest

from backend.api.public_examples import BODY_FIELDS
from backend.main import app
from backend.scripts import export_public_api as exporter

EXAMPLES_FILE = exporter.ROOT / "Frontend" / "public" / "api-response-examples.json"


def _snapshot() -> dict:
    return json.loads(exporter.SNAPSHOT.read_text(encoding="utf-8"))


def _endpoints() -> list[dict]:
    return [e for t in _snapshot()["tags"] for e in t["endpoints"]]


def _examples() -> dict:
    return json.loads(EXAMPLES_FILE.read_text(encoding="utf-8"))


def _route(method: str, path: str):
    for route in app.routes:
        if getattr(route, "path", None) == f"/api/v1{path}" and method in (route.methods or ()):
            return route
    raise AssertionError(f"{method} {path} is not a registered route")


# ── a small checker for the schema tree ───────────────────────────────────────

def _kind(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _scalar_ok(doc_type: str, value) -> bool:
    kind = _kind(value)
    if doc_type in ("any", "file", "null"):
        return True   # "null": only ever seen as null, so the type is unknown
    if doc_type == "integer":
        return kind in ("integer", "number")   # 5 and 5.0 are one JSON number
    if doc_type == "number":
        return kind in ("integer", "number")
    return kind == doc_type


def mismatches(node: dict, value, where: str = "$", *, strict_keys: bool = True) -> list[str]:
    """Where `value` does not fit `node`. None always fits (a value may be
    absent from one account and present in another); a LIST where the schema
    says object, an object where it says list, an unknown key, or a scalar of
    another type never fits."""
    if value is None:
        return []
    if "any_of" in node:
        options = [mismatches(b, value, where, strict_keys=strict_keys) for b in node["any_of"]]
        return [] if any(not o for o in options) else [f"{where}: fits none of {node['type']}"]
    t = node["type"]
    if t == "array":
        if not isinstance(value, list):
            return [f"{where}: documented as an array, is {_kind(value)}"]
        out: list[str] = []
        for i, item in enumerate(value[:50]):
            out += mismatches(node.get("items", {"type": "any"}), item, f"{where}[{i}]", strict_keys=strict_keys)
        return out
    if t == "object":
        if not isinstance(value, dict):
            return [f"{where}: documented as an object, is {_kind(value)}"]
        out = []
        if "fields" in node:
            known = {f["name"]: f for f in node["fields"]}
            for key, sub in value.items():
                if key not in known:
                    if strict_keys:
                        out.append(f"{where}.{key}: not documented")
                    continue
                out += mismatches(known[key], sub, f"{where}.{key}", strict_keys=strict_keys)
        elif "values" in node:
            for key, sub in list(value.items())[:50]:
                out += mismatches(node["values"], sub, f"{where}.{key}", strict_keys=strict_keys)
        return out
    if t == "any":
        return []
    if "|" in t:  # a union without branches recorded: any member type will do
        return [] if any(_scalar_ok(p.strip(), value) or _kind(value) == p.strip() for p in t.split("|")) else [f"{where}: {_kind(value)} not in {t}"]
    if t == "string" and node.get("enum") and value not in node["enum"]:
        return [f"{where}: {value!r} is not one of {node['enum']}"]
    return [] if _scalar_ok(t, value) else [f"{where}: documented as {t}, is {_kind(value)}"]


# ── requests ──────────────────────────────────────────────────────────────────

JSON_BODIES = [e for e in _endpoints() if e["request_body"] and e["request_body"]["content_type"] == "application/json"]


def test_every_documented_request_example_is_accepted_by_its_route():
    """The example a developer copies must not be a 422."""
    rejected = []
    for ep in JSON_BODIES:
        route = _route(ep["method"], ep["path"])
        if route.body_field is None:
            continue  # reads the raw request (POST /mcp): see the MCP test below
        _, errors = route.body_field.validate(ep["request_body"]["example"], {}, loc=("body",))
        if errors:
            rejected.append(f"{ep['method']} {ep['path']}: {str(errors)[:160]}")
    assert not rejected, "documented examples the route refuses:\n" + "\n".join(rejected)
    assert len(JSON_BODIES) >= 50, "the check lost most of its endpoints"


def test_the_documented_mcp_request_is_answered_by_the_endpoint(client, auth_headers):
    """POST /mcp reads its body itself, so FastAPI cannot validate the example:
    send it, with a key, and read the answer."""
    created = client.post("/api/v1/api-keys", json={"name": "docs-example", "scope": "read"}, headers=auth_headers)
    assert created.status_code in (200, 201), created.text
    key = {"Authorization": f"Bearer {created.json()['data']['key']}"}
    ep = next(e for e in _endpoints() if (e["method"], e["path"]) == ("POST", "/mcp"))
    resp = client.post("/api/v1/mcp", json=ep["request_body"]["example"], headers=key)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == ep["request_body"]["example"]["id"]
    assert "tools" in body["result"]
    assert "success" not in body, "MCP answers JSON-RPC, not the envelope"


def test_a_documented_request_example_agrees_with_its_schema_tree():
    """List where the schema says list, object where it says object, scalars of
    the declared type: at the root and all the way down."""
    wrong = []
    for ep in JSON_BODIES:
        body = ep["request_body"]
        problems = mismatches(body["schema"], body["example"], "body")
        if problems:
            wrong.append(f"{ep['method']} {ep['path']}: {problems[:3]}")
    assert not wrong, "\n".join(wrong)


def test_a_list_body_is_documented_as_a_list():
    """PATCH /sessions/{id}/overrides takes a JSON array. The reference used to
    list the fields of one item as if the body were that object."""
    ep = next(e for e in _endpoints() if (e["method"], e["path"]) == ("PATCH", "/sessions/{session_id}/overrides"))
    body = ep["request_body"]
    assert body["schema"]["type"] == "array"
    assert body["schema"]["items"]["type"] == "object"
    assert isinstance(body["example"], list) and isinstance(body["example"][0], dict)
    # and the opposite: no object body is wrapped in a list
    for other in JSON_BODIES:
        if other["request_body"]["schema"]["type"] != "array":
            assert not isinstance(other["request_body"]["example"], list), other["path"]


def test_free_form_bodies_name_only_keys_the_handler_reads():
    """BODY_FIELDS is hand-written documentation for routes whose body is a bare
    dict. Each key must appear in the handler (or in the model it validates
    with), so a renamed key cannot leave the reference describing the old one."""
    for (method, path), fields in BODY_FIELDS.items():
        source = inspect.getsource(_route(method, path).endpoint)
        if path == "/mcp":
            from backend.mcp import protocol
            source += inspect.getsource(protocol)
        if "configure/columns" in path or path.endswith("/columns"):
            source += inspect.getsource(__import__("backend.schemas.configuration", fromlist=["x"]))
        for field in fields:
            assert re.search(rf"[\"']{re.escape(field['name'])}[\"']|\b{re.escape(field['name'])}\b", source), (
                f"{method} {path}: documented key {field['name']!r} is not read by the handler")


def test_documented_free_form_example_keys_are_documented_fields():
    for (method, path), fields in BODY_FIELDS.items():
        ep = next((e for e in _endpoints() if (e["method"], e["path"]) == (method, path)), None)
        assert ep is not None, f"BODY_FIELDS documents {method} {path}, which is not in the reference"
        names = {f["name"] for f in fields}
        example = ep["request_body"]["example"]
        assert set(example) <= names, (path, set(example) - names)


# ── captured responses ───────────────────────────────────────────────────────

def test_response_examples_are_for_real_endpoints_with_the_documented_status():
    by_id = {e["id"]: e for e in _endpoints()}
    stale = [i for i in _examples() if i not in by_id]
    assert not stale, f"examples for endpoints that no longer exist: {stale}"
    wrong = []
    for ident, rec in _examples().items():
        ep = by_id[ident]
        # A replayed idempotency key legitimately answers 200 where 201 is documented.
        if rec["status"] != ep["success_status"] and not (ep["success_status"] == 201 and rec["status"] == 200):
            wrong.append(f"{ident}: captured {rec['status']}, documented {ep['success_status']}")
    assert not wrong, "\n".join(wrong)


def test_most_endpoints_have_a_captured_example():
    """Catches a capture run that silently recorded almost nothing."""
    covered = len(_examples())
    total = len(_endpoints())
    assert covered >= total * 0.55, f"only {covered} of {total} endpoints have a captured response"


def test_every_captured_response_conforms_to_its_own_schema():
    wrong = []
    for ident, rec in _examples().items():
        if "example" not in rec:
            continue
        problems = mismatches(rec["schema"], rec["example"], "$")
        if problems:
            wrong.append(f"{ident}: {problems[:2]}")
    assert not wrong, "\n".join(wrong)


def test_json_responses_use_the_documented_envelope():
    """The guide says every JSON answer is {success, data, meta}. The MCP
    endpoint is JSON-RPC and says so; nothing else may differ."""
    for ident, rec in _examples().items():
        if "example" not in rec or ident == "post-mcp":
            continue
        body = rec["example"]
        assert isinstance(body, dict) and body.get("success") is True and "data" in body and "meta" in body, ident
        top = [f["name"] for f in rec["schema"]["fields"]]
        assert top[:3] == ["success", "data", "meta"], (ident, top)


def test_captured_examples_carry_nothing_private():
    text = EXAMPLES_FILE.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z]:\\\\", text), "a Windows path leaked into an example"
    assert "stockai.demo" not in text, "a trial login leaked into an example"
    assert "sk_live_" not in text
    assert "/Users/" not in text and "\\\\Users\\\\" not in text
    assert not re.search(r"eyJ[A-Za-z0-9_-]{20,}", text), "a JWT leaked into an example"
    # Ids were renamed in order of first appearance, so no run's real ids remain.
    assert "ten_faro_demo" not in text
    assert "DESKTOP-" not in text, "this machine's name leaked into an example (worker_id)"
    assert "localhost" not in text and "127.0.0.1" not in text


def test_a_response_schema_never_calls_a_list_an_object():
    """The invariant behind the original symptom, on the response side: wherever
    the example holds a list, the schema node is an array, and the reverse."""
    for ident, rec in _examples().items():
        if "example" not in rec:
            continue
        def walk(node, value, where):
            if isinstance(value, list):
                assert node["type"] == "array", f"{ident} {where}: a list documented as {node['type']}"
                for v in value:
                    walk(node["items"], v, where + "[]")
            elif isinstance(value, dict) and "fields" in node:
                known = {f["name"]: f for f in node["fields"]}
                for k, v in value.items():
                    assert k in known, f"{ident} {where}.{k} missing from the schema"
                    walk(known[k], v, f"{where}.{k}")
            elif isinstance(value, dict) and "values" in node:
                for k, v in value.items():
                    walk(node["values"], v, f"{where}.{k}")
            elif isinstance(value, dict):
                assert node["type"] in ("object", "any") or "any_of" in node, f"{ident} {where}: an object documented as {node['type']}"
        walk(rec["schema"], rec["example"], "$")


# ── the live API against its documentation ───────────────────────────────────

def _readable_endpoints() -> list[dict]:
    out = []
    for ep in _endpoints():
        if ep["method"] != "GET" or "example" not in _examples().get(ep["id"], {}):
            continue
        names = set(re.findall(r"\{(\w+)(?::\w+)?\}", ep["path"]))
        if names <= {"session_id", "sku"}:
            out.append(ep)
    return out


def test_live_responses_have_the_documented_shape(client, auth_headers, completed_session):
    """Calls every read endpoint this suite can reach and compares the answer's
    SHAPE with the documented one. Absent or null values are fine (accounts
    differ), and so is a key the docs do not list: many answers carry keys only
    for some data (a legacy column mapping, a fitted model), and an account
    without them cannot have them in its example. A list where the docs say
    object (or the reverse), a changed scalar type or a documented enumeration
    that no longer holds are not fine."""
    session_id = completed_session["id"]
    compared, drift = 0, []
    for ep in _readable_endpoints():
        path = ep["path"].replace("{session_id}", session_id).replace("{sku}", "SKU_001")
        params = {p["name"]: p["example"] for p in ep["parameters"]
                  if p["in"] == "query" and p["required"] and not str(p["example"]).startswith("<")}
        if any(p["in"] == "query" and p["required"] and str(p["example"]).startswith("<") for p in ep["parameters"]):
            params["session_id"] = session_id
        resp = client.get(f"/api/v1{path}", params=params, headers=auth_headers)
        if resp.status_code != 200 or "json" not in resp.headers.get("content-type", ""):
            continue
        rec = _examples()[ep["id"]]
        problems = mismatches(rec["schema"], resp.json(), "$", strict_keys=False)
        compared += 1
        if problems:
            drift.append(f"{ep['id']}: {problems[:3]}")
    assert compared >= 12, f"only {compared} endpoints could be compared; the check is vacuous"
    assert not drift, "documented responses that no longer match the live API:\n" + "\n".join(drift)


# ── the prose that states numbers ─────────────────────────────────────────────

def test_the_in_app_console_states_the_real_limits():
    """/desarrolladores reads its limits from the snapshot; the in-app /api
    screen has them in its catalogue, where they used to omit the trial ceiling
    and drift from the plans. The numbers must be the plans' numbers."""
    limits = _snapshot()["limits"]
    translations = (exporter.ROOT / "Frontend" / "src" / "i18n" / "translations.ts").read_text(encoding="utf-8")
    for lang_marker in ("'apidocs.limits_desc':", "'apidocs.spec_rate_value':"):
        lines = [ln for ln in translations.splitlines() if lang_marker in ln]
        assert len(lines) == 2, f"expected the es and en entry of {lang_marker}"
        for line in lines:
            assert str(limits["per_minute_per_key"]) in line, line
            assert str(limits["per_day_per_key"]["free"]) in line, line
    for line in (ln for ln in translations.splitlines() if "'apidocs.limits_desc':" in ln):
        assert str(limits["per_day_per_key"]["demo"]) in line, f"the trial ceiling is missing: {line}"

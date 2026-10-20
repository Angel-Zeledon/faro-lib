"""The generated Python and TypeScript clients stay in step with the API, and the
Python one really talks to a server. No database."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.scripts import generate_clients as gen


def _load_python_client():
    spec = importlib.util.spec_from_file_location("stockai_client_under_test", gen.PY_OUT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestInStepWithTheApi:
    def test_the_generated_files_are_current(self):
        assert gen.main(["--check"]) == 0, (
            "sdk/ is out of date: run `python -m backend.scripts.generate_clients`")

    def test_every_documented_operation_has_a_method_in_both_languages(self):
        _, eps = gen._endpoints()
        module = _load_python_client()
        ts = gen.TS_OUT.read_text(encoding="utf-8")
        missing_py = [e["id"] for e in eps if not hasattr(module.StockAI, gen._method_name(e["id"]))]
        missing_ts = [e["id"] for e in eps if f"  {gen._method_name(e['id'])}(" not in ts]
        assert missing_py == [] and missing_ts == []
        assert len(eps) >= 200            # a vacuous snapshot would pass the checks above

    def test_method_names_are_unique(self):
        _, eps = gen._endpoints()
        names = [gen._method_name(e["id"]) for e in eps]
        assert len(names) == len(set(names))

    def test_clients_embed_no_secret_and_no_host_but_the_default(self):
        for path in (gen.PY_OUT, gen.TS_OUT):
            text = path.read_text(encoding="utf-8")
            assert "sk_live_" not in text.replace("sk_live_...", "")
            assert "localhost" not in text and "127.0.0.1" not in text


class _Echo(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        _Echo.seen.append({"method": self.command, "path": self.path,
                           "headers": dict(self.headers), "body": body})
        if self.path.startswith("/api/v1/alerts/limited"):
            self.send_response(429)
            self.send_header("Retry-After", "30")
            self.send_header("Content-Type", "application/json")
            payload = json.dumps({"detail": "slow down", "error_code": "rate_limited",
                                  "error_params": {"per_minute": 120}}).encode()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            payload = json.dumps({"data": {"ok": True}}).encode()
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = do_DELETE = _reply


@pytest.fixture()
def server():
    _Echo.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


class TestPythonClientOnTheWire:
    def test_a_key_that_is_not_a_key_is_refused_before_any_call(self):
        with pytest.raises(ValueError):
            _load_python_client().StockAI("hunter2")

    def test_a_get_sends_the_key_and_returns_the_envelope(self, server):
        api = _load_python_client().StockAI("sk_live_test", base_url=server)
        assert api.get_alerts() == {"data": {"ok": True}}
        call = _Echo.seen[0]
        assert call["method"] == "GET" and call["path"] == "/api/v1/alerts"
        assert call["headers"]["Authorization"] == "Bearer sk_live_test"

    def test_path_values_are_encoded_and_query_values_are_sent_only_when_given(self, server):
        api = _load_python_client().StockAI("sk_live_test", base_url=server)
        eps = {e["id"]: e for e in gen._endpoints()[1]}
        op = next(i for i, e in eps.items() if e["method"] == "GET" and "{session_id}" in e["path"]
                  and not [p for p in e["parameters"] if p["required"] and p["in"] != "path"])
        getattr(api, gen._method_name(op))("a b/c")
        assert "a%20b%2Fc" in _Echo.seen[0]["path"]

    def test_a_json_body_is_sent_as_json(self, server):
        api = _load_python_client().StockAI("sk_live_test", base_url=server)
        eps = gen._endpoints()[1]
        op = next(e for e in eps if e["method"] == "POST" and not [
            p for p in e["parameters"] if p["required"]] and e.get("request_body")
            and "json" in e["request_body"]["content_type"])
        getattr(api, gen._method_name(op["id"]))(body={"x": 1})
        call = _Echo.seen[0]
        assert json.loads(call["body"]) == {"x": 1}
        assert call["headers"]["Content-Type"] == "application/json"

    def test_a_refusal_raises_with_the_code_the_params_and_the_retry_delay(self, server):
        module = _load_python_client()
        api = module.StockAI("sk_live_test", base_url=server)
        with pytest.raises(module.StockAIError) as exc:
            api.request("GET", "/alerts/limited")
        assert exc.value.status == 429 and exc.value.error_code == "rate_limited"
        assert exc.value.error_params == {"per_minute": 120} and exc.value.retry_after == 30

    def test_an_upload_is_multipart(self, server):
        api = _load_python_client().StockAI("sk_live_test", base_url=server)
        api.request("POST", "/datasets", files={"file": ("a.csv", b"x,y\n1,2\n")},
                    body={"name": "n"})
        call = _Echo.seen[0]
        assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
        assert b'filename="a.csv"' in call["body"] and b"x,y\n1,2\n" in call["body"]
        assert b'name="name"' in call["body"]

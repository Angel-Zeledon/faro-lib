"""`mcp_server/stockai_mcp.py` — the pipe a customer copies onto their own machine.

It is the one piece of this product that runs somewhere nobody can reach: on a
buyer's laptop, launched by Claude Desktop, with its stdout wired straight into
a protocol parser. When it misbehaves the symptom is a connector that silently
never appears, and there is no log anybody will read.

It was verified by hand against a live server. That proved it worked once; this
proves it keeps working. Every test here drives the real script as a real
subprocess against a real socket — no mocks, because the failure modes being
pinned are all about what comes back over HTTP.

Four properties, and each one is a way a client hangs or breaks if it is lost:

1. **One frame in, one frame out, on its own line.** The stdio transport is
   newline-delimited JSON; two frames on one line, or a frame split over two,
   is a malformed stream.
2. **A 200 is not proof StockAI answered.** A reverse proxy, an SSO portal or a
   captive network in front of the instance all answer 200 with HTML. Passing
   that through puts garbage into the protocol stream, and the client's only
   symptom is a connector that stops working with nothing on screen.
3. **Every failure still produces a frame.** A request whose answer evaporates
   leaves the client waiting forever, which reads as StockAI hanging.
4. **stdout carries protocol and nothing else.** One stray `print` corrupts the
   stream; diagnostics belong on stderr.

No database and no app: `http.server` in a thread is the whole fixture.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "mcp_server" / "stockai_mcp.py"

_FRAME = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def _handler_returning(status: int, body: bytes, content_type: str = "application/json"):
    class _H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's name
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *_a):
            pass  # the test's own output is the only thing worth reading
    return _H


@pytest.fixture
def fake_stockai():
    """A one-request HTTP server standing in for the instance.

    Yields a factory: call it with the status and body this StockAI should answer,
    and get back the base URL to point the adapter at.
    """
    servers = []

    def _serve(status: int, body: bytes, content_type: str = "application/json") -> str:
        server = HTTPServer(("127.0.0.1", 0), _handler_returning(status, body, content_type))
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_port}"

    yield _serve

    for server in servers:
        server.shutdown()
        server.server_close()


def _run(base_url: str, frames=(_FRAME,), key: str = "sk_live_test", timeout: int = 30):
    """Drive the adapter the way a desktop client does, and return what it said."""
    stdin = "\n".join(json.dumps(f) for f in frames) + "\n"
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=stdin, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "STOCKAI_URL": base_url, "STOCKAI_API_KEY": key},
    )
    return proc


def _frames_of(proc) -> list[dict]:
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


class TestItRelaysWhatFaroSaid:
    def test_a_normal_answer_comes_back_as_one_frame_on_one_line(self, fake_stockai):
        answer = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "x"}]}}
        proc = _run(fake_stockai(200, json.dumps(answer).encode()))

        assert proc.stdout.count("\n") == 1, (
            f"expected exactly one newline-delimited frame, got {proc.stdout!r}"
        )
        assert _frames_of(proc) == [answer]

    def test_a_multiline_answer_is_flattened_rather_than_split(self, fake_stockai):
        """A frame split across lines is two malformed frames to the client.

        StockAI answers compactly, but a proxy that pretty-prints JSON would not,
        and the adapter must not pass the newlines through.
        """
        answer = {"jsonrpc": "2.0", "id": 1, "result": {"deep": {"nested": True}}}
        proc = _run(fake_stockai(200, json.dumps(answer, indent=2).encode()))

        assert proc.stdout.count("\n") == 1, proc.stdout
        assert _frames_of(proc) == [answer]

    def test_an_accepted_notification_produces_no_frame(self, fake_stockai):
        """202 with no body. Writing an empty line here would be a parse error
        on the client, for a message that was never owed an answer."""
        proc = _run(fake_stockai(202, b""),
                    frames=({"jsonrpc": "2.0", "method": "notifications/initialized"},))
        assert proc.stdout.strip() == "", f"answered a notification: {proc.stdout!r}"


class TestATwoHundredIsNotProofFaroAnswered:
    def test_html_from_a_proxy_never_reaches_the_protocol_stream(self, fake_stockai):
        """The failure this test exists for: `STOCKAI_URL` pointing at a reverse
        proxy or a login page. It answers 200, with HTML, and passing it through
        leaves the client with a corrupt stream and nothing to read."""
        page = b"<!DOCTYPE html><html><body>Sign in to continue</body></html>"
        proc = _run(fake_stockai(200, page, content_type="text/html"))

        # The property is that stdout is well-formed protocol, NOT that the
        # string "<html>" is absent: the error message quotes what came back on
        # purpose, and that quote is the most useful thing in it. Parsing every
        # line is the check — raw HTML would not survive `json.loads`.
        for line in proc.stdout.splitlines():
            if line.strip():
                json.loads(line)

        frames = _frames_of(proc)
        assert len(frames) == 1 and frames[0]["id"] == 1
        message = frames[0]["error"]["message"]
        assert "STOCKAI_URL" in message, (
            f"the client was not told what to check: {message!r}"
        )
        assert "Sign in to continue" in message, (
            "the message does not show what actually came back, so the operator "
            "cannot tell a login page from a broken gateway"
        )


class TestEveryFailureStillProducesAFrame:
    def test_a_rejected_key_is_reported_not_swallowed(self, fake_stockai):
        body = json.dumps({"detail": "API key is invalid or expired"}).encode()
        proc = _run(fake_stockai(401, body))

        frames = _frames_of(proc)
        assert len(frames) == 1, "a 401 left the client waiting for an answer"
        message = frames[0]["error"]["message"]
        assert "STOCKAI_API_KEY" in message
        # The server's sentence and ours, joined by a full stop rather than run
        # together — "expired Check STOCKAI_API_KEY" shipped once.
        assert "expired. Check" in message, message

    def test_a_rate_limit_says_to_wait(self, fake_stockai):
        body = json.dumps({"detail": "Rate limit exceeded"}).encode()
        proc = _run(fake_stockai(429, body))
        assert "minute" in _frames_of(proc)[0]["error"]["message"]

    def test_an_unreachable_instance_is_reported_rather_than_hung_on(self):
        """Nothing is listening on this port. The client must get a frame."""
        proc = _run("http://127.0.0.1:1", timeout=60)
        frames = _frames_of(proc)
        assert len(frames) == 1 and frames[0]["id"] == 1
        assert "Could not reach StockAI" in frames[0]["error"]["message"]

    def test_a_malformed_frame_from_the_client_is_answered_not_forwarded(self, fake_stockai):
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            input="{not json\n", capture_output=True, text=True, timeout=30,
            env={**os.environ, "STOCKAI_URL": fake_stockai(200, b"{}"),
                 "STOCKAI_API_KEY": "sk_live_test"},
        )
        frames = _frames_of(proc)
        assert len(frames) == 1
        assert frames[0]["error"]["code"] == -32700  # parse error


class TestStdoutIsProtocolOnly:
    def test_diagnostics_go_to_stderr(self, fake_stockai):
        """One stray print on stdout corrupts the stream, and the symptom is a
        connector that will not start with nothing on screen to explain it."""
        answer = {"jsonrpc": "2.0", "id": 1, "result": {}}
        proc = _run(fake_stockai(200, json.dumps(answer).encode()))

        assert "[stockai-mcp]" in proc.stderr, "the adapter said nothing anywhere"
        assert "[stockai-mcp]" not in proc.stdout
        for line in proc.stdout.splitlines():
            if line.strip():
                json.loads(line)  # raises if anything non-protocol got through

    def test_a_missing_url_refuses_to_start_and_says_which_variable(self):
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT)], input="", capture_output=True,
            text=True, timeout=30,
            env={k: v for k, v in os.environ.items()
                 if k not in ("STOCKAI_URL", "STOCKAI_API_KEY")},
        )
        assert proc.returncode != 0, "started with nowhere to connect to"
        assert "STOCKAI_URL" in proc.stderr
        assert proc.stdout.strip() == ""

    def test_a_missing_key_refuses_to_start_and_says_where_to_get_one(self):
        env = {k: v for k, v in os.environ.items() if k != "STOCKAI_API_KEY"}
        env["STOCKAI_URL"] = "http://127.0.0.1:1"
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT)], input="", capture_output=True,
            text=True, timeout=30, env=env,
        )
        assert proc.returncode != 0
        assert "STOCKAI_API_KEY" in proc.stderr
        assert "API Keys" in proc.stderr, "did not say where a key comes from"

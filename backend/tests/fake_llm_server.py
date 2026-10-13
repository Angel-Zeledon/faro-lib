"""A local stand-in for an AI provider, for tests that must never reach one.

It listens on 127.0.0.1 and answers from a script: each request pops the next
`Reply` (status, headers, body, delay), or the last one repeats. Every request
is logged with its method, path, headers and body, so a test can assert what
was SENT (the key header, the payload shape) and how many times (retries).

The canned bodies below reproduce each provider's real success and error
shapes (OpenAI / Groq / OpenRouter / DeepSeek chat completions, Anthropic
Messages, Gemini's compat-layer list-wrapped errors).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional


@dataclass
class Reply:
    status: int = 200
    body: Any = None                  # dict/list -> JSON; bytes/str -> raw
    headers: dict = field(default_factory=dict)
    delay_s: float = 0.0              # sleep before answering (timeouts)
    drop: bool = False                # read the request, then hang up without answering


@dataclass
class Seen:
    method: str
    path: str
    headers: dict
    body: Any


class FakeProvider:
    def __init__(self):
        self.script: list[Reply] = []
        self.by_path: dict[str, list[Reply]] = {}
        self.requests: list[Seen] = []
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _handle(self):
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = raw
                with fake._lock:
                    fake.requests.append(Seen(self.command, self.path,
                                              {k.lower(): v for k, v in self.headers.items()}, body))
                    reply = fake._next(self.path)
                if reply.delay_s:
                    time.sleep(reply.delay_s)
                if reply.drop:
                    self.close_connection = True
                    try:
                        self.connection.shutdown(2)
                    except OSError:
                        pass
                    return
                payload = reply.body
                if isinstance(payload, (dict, list)):
                    data = json.dumps(payload).encode()
                    ctype = "application/json"
                elif isinstance(payload, str):
                    data = payload.encode()
                    ctype = "application/json"
                else:
                    data = payload or b""
                    ctype = "application/json"
                try:
                    self.send_response(reply.status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    for k, v in reply.headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            do_GET = do_POST = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _next(self, path: str) -> Reply:
        bare = path.split("?", 1)[0]
        for key, queue in self.by_path.items():
            if bare.endswith(key) and queue:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        if not self.script:
            return Reply(200, openai_ok("default"))
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]

    def reply(self, *replies: Reply) -> "FakeProvider":
        self.script = list(replies)
        return self

    def on(self, path_suffix: str, *replies: Reply) -> "FakeProvider":
        self.by_path[path_suffix] = list(replies)
        return self

    def calls(self, path_suffix: str = "") -> list[Seen]:
        return [r for r in self.requests if r.path.split("?", 1)[0].endswith(path_suffix)]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ── Real-shaped bodies ───────────────────────────────────────────────────────

def openai_ok(text: str, *, prompt_tokens: int = 11, completion_tokens: int = 7,
              tool_calls: Optional[list] = None, finish: str = "stop") -> dict:
    message: dict = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"id": "chatcmpl-1", "object": "chat.completion", "model": "m",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens}}


def openai_error(message: str, etype: str, code: Optional[str] = None) -> dict:
    return {"error": {"message": message, "type": etype, "param": None, "code": code}}


def anthropic_ok(text: str = "", *, tool_use: Optional[dict] = None,
                 stop: str = "end_turn") -> dict:
    content: list = [{"type": "thinking", "thinking": "", "signature": "x"}]
    if text:
        content.append({"type": "text", "text": text})
    if tool_use:
        content.append({"type": "tool_use", **tool_use})
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude",
            "content": content, "stop_reason": "tool_use" if tool_use else stop,
            "usage": {"input_tokens": 21, "output_tokens": 9}}


def anthropic_error(etype: str, message: str) -> dict:
    return {"type": "error", "error": {"type": etype, "message": message},
            "request_id": "req_1"}


def gemini_error(code: int, status: str, message: str) -> list:
    return [{"error": {"code": code, "message": message, "status": status}}]


def models(*ids: str, free: tuple[str, ...] = ()) -> dict:
    return {"object": "list", "data": [
        {"id": i, "object": "model",
         "pricing": {"prompt": "0" if i in free else "0.000001",
                     "completion": "0" if i in free else "0.000002"}}
        for i in ids]}

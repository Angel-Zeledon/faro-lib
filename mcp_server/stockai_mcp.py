#!/usr/bin/env python3
"""StockAI MCP over stdio — a pipe, not a second server.

Claude Desktop and most desktop MCP clients speak stdio: they launch a process
and exchange newline-delimited JSON-RPC over its stdin/stdout. StockAI's MCP server
speaks HTTP (`POST /api/v1/mcp`). This file is the twenty lines of pipe between
the two.

It deliberately holds NO catalogue, no tool logic and no schema. It reads a
frame, posts it, writes what comes back. Everything a client can ask for is
decided by `backend/mcp/catalog.py` on the server, so a tool added or fixed
there needs no new copy of this file distributed to anybody.

Standard library only — no pip install, no virtualenv. A customer's IT
department is asked to copy one file and set two environment variables.

    STOCKAI_URL       https://stockai.example.com     (their instance, no trailing /)
    STOCKAI_API_KEY   sk_live_...                  (app -> /automatizacion -> API Keys)

Claude Desktop configuration:

    {
      "mcpServers": {
        "stockai": {
          "command": "python",
          "args": ["/absolute/path/to/stockai_mcp.py"],
          "env": {
            "STOCKAI_URL": "https://stockai.example.com",
            "STOCKAI_API_KEY": "sk_live_..."
          }
        }
      }
    }

Everything this process prints on stdout is protocol. Diagnostics go to stderr,
because one stray print on stdout corrupts the stream and the client's only
symptom is a connector that will not start.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-11-25"
TIMEOUT_SECONDS = 60

# JSON-RPC reserved codes, repeated here rather than imported: this file is
# copied to a customer's machine on its own and must not need the repository.
PARSE_ERROR = -32700
INTERNAL_ERROR = -32603


def log(message: str) -> None:
    print(f"[stockai-mcp] {message}", file=sys.stderr, flush=True)


def endpoint() -> str:
    base = (os.environ.get("STOCKAI_URL") or "").strip().rstrip("/")
    if not base:
        log("STOCKAI_URL is not set. Point it at your StockAI instance, e.g. "
            "https://stockai.example.com")
        sys.exit(2)
    return f"{base}/api/v1/mcp"


def api_key() -> str:
    key = (os.environ.get("STOCKAI_API_KEY") or "").strip()
    if not key:
        log("STOCKAI_API_KEY is not set. Generate a key in StockAI at "
            "/automatizacion, under the API Keys tab (read-only is enough).")
        sys.exit(2)
    return key


def post(url: str, key: str, payload: bytes) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {key}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "User-Agent": "stockai-mcp-stdio/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        # 4xx and 5xx arrive here with a readable body — which is where the
        # "your key is invalid" and "rate limited" messages are. Passing the
        # status up lets the caller turn them into something the model sees
        # rather than a dead connection.
        return exc.code, exc.read()


def error_frame(request_id, message: str, code: int = INTERNAL_ERROR) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def http_failure_message(status: int, body: bytes) -> str:
    """What to tell the model when the server refused the call."""
    detail = ""
    try:
        parsed = json.loads(body or b"{}")
        detail = parsed.get("detail") or parsed.get("error", {}).get("message") or ""
    except (ValueError, AttributeError):
        detail = (body or b"").decode("utf-8", "replace")[:300]

    # The server's own sentence may or may not end in a full stop. Joining
    # without checking put "API key is invalid or expired Check STOCKAI_API_KEY"
    # in the client's face.
    if detail and detail[-1] not in ".!?":
        detail += "."

    if status == 401:
        return (detail or "StockAI rejected the API key.") + \
            " Check STOCKAI_API_KEY, and that the key has not been revoked."
    if status == 429:
        return (detail or "StockAI is rate limiting this key.") + " Try again in a minute."
    return f"StockAI returned HTTP {status}. {detail}".strip()


def main() -> None:
    url = endpoint()
    key = api_key()
    log(f"relaying to {url}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        # The id is needed to answer a transport failure, and it has to be read
        # before the frame leaves: a client whose request evaporates waits for
        # an answer that never comes, which looks like StockAI hanging.
        try:
            request_id = json.loads(line).get("id")
        except ValueError:
            print(json.dumps(error_frame(None, "Invalid JSON", PARSE_ERROR)), flush=True)
            continue

        try:
            status, body = post(url, key, line.encode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log(f"could not reach StockAI: {exc}")
            if request_id is not None:
                print(json.dumps(error_frame(
                    request_id, f"Could not reach StockAI at {url}: {exc}")), flush=True)
            continue

        if status == 202 or not body:
            # A notification the server accepted. Nothing to write back — and
            # writing an empty line would be a malformed frame.
            continue

        if status >= 400:
            message = http_failure_message(status, body)
            log(message)
            if request_id is not None:
                print(json.dumps(error_frame(request_id, message)), flush=True)
            continue

        # A 200 is not proof that StockAI answered. A reverse proxy, an SSO portal
        # or a captive network in front of the instance all answer 200 with
        # HTML, and writing that to stdout puts a malformed frame into the
        # protocol stream — where the client's only symptom is a connector that
        # stops working with nothing on screen.
        #
        # So: parse before writing. The frame is still the server's, field for
        # field; re-serialising it compactly is also what guarantees one frame
        # per line, which the stdio transport requires.
        try:
            frame = json.loads(body)
        except ValueError:
            message = (
                f"{url} answered 200 with something that is not JSON "
                f"({body[:120]!r}). Check that STOCKAI_URL points at StockAI itself "
                f"and not at a proxy or a login page."
            )
            log(message)
            if request_id is not None:
                print(json.dumps(error_frame(request_id, message)), flush=True)
            continue

        print(json.dumps(frame, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

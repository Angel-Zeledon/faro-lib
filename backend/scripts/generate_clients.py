"""Generate the Python and TypeScript API clients from the public API snapshot.

    backend/.venv/Scripts/python.exe -m backend.scripts.generate_clients
    backend/.venv/Scripts/python.exe -m backend.scripts.generate_clients --check

The input is `Frontend/src/data/public-api.json`, which is itself generated from the
running app's OpenAPI document (`export_public_api.py`) and checked by a test, so a
route added to or removed from the key-callable surface moves the snapshot, and the
snapshot moves these two files: `test_sdk_clients.py` runs `--check` and turns red
until they are regenerated. The clients therefore cannot describe an API that no
longer exists.

One method per operation, named by the operation id (`get_alerts`,
`post_sessions_session_id_analyst_narrate`): ids are unique and stable, a prettier
name would need a hand-kept table that drifts. Path parameters are positional,
query and header parameters are keyword arguments, a JSON body is `body`, an upload
is `files`. Routes declare no response model, so a call returns the parsed JSON
envelope as it came (or the bytes of a file) - the clients do not pretend a type
the server does not promise.

The files are standard-library only (urllib in Python, fetch in TypeScript): a
customer drops one file into their project, with no dependency to audit.
"""
from __future__ import annotations

import argparse
import json
import keyword
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "Frontend" / "src" / "data" / "public-api.json"
PY_OUT = ROOT / "sdk" / "python" / "stockai_client.py"
TS_OUT = ROOT / "sdk" / "typescript" / "stockai-client.ts"


def _endpoints() -> tuple[str, list[dict]]:
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    eps = [e for t in snap["tags"] for e in t["endpoints"]]
    return snap["base_path"], sorted(eps, key=lambda e: e["id"])


def _ident(name: str) -> str:
    out = re.sub(r"\W", "_", name).strip("_").lower() or "param"
    if out[0].isdigit():
        out = "_" + out
    return out + "_" if keyword.iskeyword(out) else out


def _method_name(op_id: str) -> str:
    return _ident(op_id.replace("-", "_"))


def _is_json_response(ep: dict) -> bool:
    kinds = ep.get("response_content_types") or ["application/json"]
    return any("json" in k for k in kinds)


def _split(ep: dict) -> tuple[list[dict], list[dict], list[dict]]:
    params = ep.get("parameters") or []
    return ([p for p in params if p["in"] == "path"],
            [p for p in params if p["in"] == "query"],
            [p for p in params if p["in"] == "header"])


def _doc(ep: dict) -> str:
    text = (ep.get("summary") or ep["id"]).replace('"""', "'''").strip()
    return f"{text}  [{ep['method']} {ep['path']}, needs a {ep['scope']} key]"


# ── Python ───────────────────────────────────────────────────────────────────

_PY_HEAD = '''"""StockAI API client (Python). GENERATED - do not edit by hand.

Regenerate with `python -m backend.scripts.generate_clients`; the test suite fails
when this file is out of step with the public API. Standard library only.

    from stockai_client import StockAI
    api = StockAI("sk_live_...")
    envelope = api.get_alerts()

A call returns the parsed JSON envelope exactly as the API sent it (or the bytes of
a file download). A refused call raises `StockAIError` carrying `status`,
`error_code`, `error_params` and, when the API sent one, `retry_after` seconds.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Optional

DEFAULT_BASE_URL = "https://app.stockai.es"
API_PREFIX = "%(base)s"


class StockAIError(Exception):
    def __init__(self, status: int, message: str, error_code: Optional[str] = None,
                 error_params: Optional[dict] = None, retry_after: Optional[int] = None):
        super().__init__(f"{status} {error_code or ''} {message}".strip())
        self.status = status
        self.message = message
        self.error_code = error_code
        self.error_params = error_params or {}
        self.retry_after = retry_after


class StockAI:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 60.0):
        if not api_key.startswith("sk_"):
            raise ValueError("api_key must be a StockAI key (sk_...)")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def request(self, method: str, path: str, *, query: Optional[dict] = None,
                headers: Optional[dict] = None, body: Any = None,
                files: Optional[dict] = None, raw: bool = False) -> Any:
        """One call. `path` is relative to the API prefix and already filled in."""
        url = self._base + API_PREFIX + path
        params = {k: v for k, v in (query or {}).items() if v is not None}
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        hdrs = {"Authorization": "Bearer " + self._key, "Accept": "application/json"}
        hdrs.update({k: str(v) for k, v in (headers or {}).items() if v is not None})
        data = None
        if files is not None:
            data, content_type = _multipart(files, body if isinstance(body, dict) else {})
            hdrs["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = resp.read()
                kind = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise _error(exc) from None
        if raw or "json" not in kind:
            return payload
        return json.loads(payload) if payload else None

'''


def _py_method(ep: dict) -> str:
    path_p, query_p, header_p = _split(ep)
    body = ep.get("request_body")
    args = ["self"] + [_ident(p["name"]) for p in path_p]
    kw: list[str] = []
    for p in query_p + header_p:
        name = _ident(p["name"])
        kw.append(f"{name}: Any" if p.get("required") else f"{name}: Any = None")
    multipart = bool(body) and "multipart" in body["content_type"]
    if body:
        if multipart:
            kw.append("files: dict")
            kw.append("body: Optional[dict] = None")
        else:
            kw.append("body: Any" if body.get("required") else "body: Any = None")
    sig = ", ".join(args + (["*"] + kw if kw else []))
    path_expr = ep["path"]
    for p in path_p:
        path_expr = path_expr.replace("{" + p["name"] + "}", "{_q(" + _ident(p["name"]) + ")}")
    path_expr = re.sub(r"\{(\w+):path\}", r"{\1}", path_expr)
    lines = [f"    def {_method_name(ep['id'])}({sig}) -> Any:",
             f'        """{_doc(ep)}"""']
    query = "{" + ", ".join(f'"{p["name"]}": {_ident(p["name"])}' for p in query_p) + "}"
    head = "{" + ", ".join(f'"{p["name"]}": {_ident(p["name"])}' for p in header_p) + "}"
    call = [f'"{ep["method"]}"', f'f"{path_expr}"']
    if query_p:
        call.append(f"query={query}")
    if header_p:
        call.append(f"headers={head}")
    if body:
        call.append("body=body")
    if multipart:
        call.append("files=files")
    if not _is_json_response(ep):
        call.append("raw=True")
    lines.append(f"        return self.request({', '.join(call)})")
    return "\n".join(lines)


_PY_TAIL = '''

def _q(value: Any) -> str:
    return urllib.parse.quote(str(value), safe="")


def _error(exc: "urllib.error.HTTPError") -> StockAIError:
    raw = exc.read()
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = {}
    retry = exc.headers.get("Retry-After")
    detail = doc.get("detail") if isinstance(doc, dict) else None
    return StockAIError(
        exc.code, detail if isinstance(detail, str) else (raw[:200].decode("utf-8", "replace")),
        error_code=doc.get("error_code") if isinstance(doc, dict) else None,
        error_params=doc.get("error_params") if isinstance(doc, dict) else None,
        retry_after=int(retry) if retry and retry.isdigit() else None)


def _multipart(files: dict, fields: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += (f"--{boundary}\\r\\nContent-Disposition: form-data; name=\\"{name}\\"\\r\\n\\r\\n"
                f"{value}\\r\\n").encode()
    for name, spec in files.items():
        filename, content = spec if isinstance(spec, tuple) else (name, spec)
        out += (f"--{boundary}\\r\\nContent-Disposition: form-data; name=\\"{name}\\"; "
                f"filename=\\"{filename}\\"\\r\\nContent-Type: application/octet-stream\\r\\n\\r\\n").encode()
        out += content + b"\\r\\n"
    out += f"--{boundary}--\\r\\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"
'''


def render_python() -> str:
    base, eps = _endpoints()
    methods = "\n\n".join(_py_method(e) for e in eps)
    return (_PY_HEAD % {"base": base}) + methods + "\n" + _PY_TAIL


# ── TypeScript ───────────────────────────────────────────────────────────────

_TS_HEAD = '''/**
 * StockAI API client (TypeScript). GENERATED - do not edit by hand.
 *
 * Regenerate with `python -m backend.scripts.generate_clients`; the test suite
 * fails when this file is out of step with the public API. No dependencies
 * (fetch only: Node 18+, Deno, Bun and browsers).
 *
 *   const api = new StockAI('sk_live_...')
 *   const envelope = await api.get_alerts()
 *
 * A call resolves to the parsed JSON envelope exactly as the API sent it (or the
 * bytes of a file download). A refused call rejects with `StockAIError`.
 */
export const DEFAULT_BASE_URL = 'https://app.stockai.es'
const API_PREFIX = '%(base)s'

export class StockAIError extends Error {
  constructor(
    public status: number,
    message: string,
    public errorCode?: string,
    public errorParams: Record<string, unknown> = {},
    public retryAfter?: number,
  ) {
    super(`${status} ${errorCode ?? ''} ${message}`.trim())
    this.name = 'StockAIError'
  }
}

type Query = Record<string, unknown>

export class StockAI {
  private base: string

  constructor(private apiKey: string, baseUrl: string = DEFAULT_BASE_URL) {
    if (!apiKey.startsWith('sk_')) throw new Error('apiKey must be a StockAI key (sk_...)')
    this.base = baseUrl.replace(/\\/+$/, '')
  }

  /** One call. `path` is relative to the API prefix and already filled in. */
  async request(method: string, path: string, opts: {
    query?: Query; headers?: Query; body?: unknown; files?: Record<string, Blob | [string, Blob]>; raw?: boolean
  } = {}): Promise<any> {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(opts.query ?? {})) {
      if (v === undefined || v === null) continue
      for (const item of Array.isArray(v) ? v : [v]) qs.append(k, String(item))
    }
    const headers: Record<string, string> = { Authorization: `Bearer ${this.apiKey}`, Accept: 'application/json' }
    for (const [k, v] of Object.entries(opts.headers ?? {})) if (v !== undefined && v !== null) headers[k] = String(v)
    let body: BodyInit | undefined
    if (opts.files) {
      const form = new FormData()
      for (const [k, v] of Object.entries((opts.body as Query) ?? {})) form.append(k, String(v))
      for (const [k, v] of Object.entries(opts.files)) {
        if (Array.isArray(v)) form.append(k, v[1], v[0]); else form.append(k, v)
      }
      body = form
    } else if (opts.body !== undefined) {
      body = JSON.stringify(opts.body)
      headers['Content-Type'] = 'application/json'
    }
    const query = qs.toString()
    const res = await fetch(`${this.base}${API_PREFIX}${path}${query ? `?${query}` : ''}`, { method, headers, body })
    if (!res.ok) {
      let doc: any = {}
      try { doc = await res.json() } catch { /* not JSON */ }
      const retry = Number(res.headers.get('Retry-After'))
      throw new StockAIError(res.status, typeof doc.detail === 'string' ? doc.detail : res.statusText,
        doc.error_code, doc.error_params, Number.isFinite(retry) && retry > 0 ? retry : undefined)
    }
    if (opts.raw || !(res.headers.get('Content-Type') ?? '').includes('json')) return new Uint8Array(await res.arrayBuffer())
    const text = await res.text()
    return text ? JSON.parse(text) : null
  }

'''


def _ts_ident(name: str) -> str:
    out = re.sub(r"\W", "_", name)
    if out[0].isdigit():
        out = "_" + out
    return out + "_" if out in _TS_RESERVED else out


_TS_RESERVED = {"break", "case", "catch", "class", "const", "continue", "debugger", "default",
                "delete", "do", "else", "enum", "export", "extends", "false", "finally", "for",
                "function", "if", "import", "in", "instanceof", "new", "null", "return", "super",
                "switch", "this", "throw", "true", "try", "typeof", "var", "void", "while", "with"}


def _ts_method(ep: dict) -> str:
    path_p, query_p, header_p = _split(ep)
    body = ep.get("request_body")
    args = [f"{_ts_ident(p['name'])}: string | number" for p in path_p]
    opts: list[str] = []
    for p in query_p:
        opts.append(f"{json.dumps(p['name'])}{'' if p.get('required') else '?'}: unknown")
    for p in header_p:
        opts.append(f"{json.dumps(p['name'])}{'' if p.get('required') else '?'}: unknown")
    multipart = bool(body) and "multipart" in body["content_type"]
    if body:
        opts.append(f"body{'' if body.get('required') else '?'}: {'Record<string, unknown>' if multipart else 'unknown'}")
        if multipart:
            opts.append("files: Record<string, Blob | [string, Blob]>")
    sig = ", ".join(args + ([f"o: {{ {'; '.join(opts)} }}" + ("" if any(
        "?" not in x.split(":")[0] for x in opts) else " = {}")] if opts else []))
    path_expr = ep["path"]
    for p in path_p:
        path_expr = path_expr.replace("{" + p["name"] + "}", "${encodeURIComponent(String(" + _ts_ident(p["name"]) + "))}")
    path_expr = re.sub(r"\{(\w+):path\}", r"${\1}", path_expr)
    parts: list[str] = []
    if query_p:
        parts.append("query: { " + ", ".join(f"{json.dumps(p['name'])}: o[{json.dumps(p['name'])}]" for p in query_p) + " }")
    if header_p:
        parts.append("headers: { " + ", ".join(f"{json.dumps(p['name'])}: o[{json.dumps(p['name'])}]" for p in header_p) + " }")
    if body:
        parts.append("body: o.body")
    if multipart:
        parts.append("files: o.files")
    if not _is_json_response(ep):
        parts.append("raw: true")
    opts_expr = "{ " + ", ".join(parts) + " }" if parts else "{}"
    return (f"  /** {_doc(ep).replace('*/', '* /')} */\n"
            f"  {_method_name(ep['id'])}({sig}): Promise<any> {{\n"
            f"    return this.request('{ep['method']}', `{path_expr}`, {opts_expr})\n"
            f"  }}")


def render_typescript() -> str:
    base, eps = _endpoints()
    return (_TS_HEAD % {"base": base}) + "\n\n".join(_ts_method(e) for e in eps) + "\n}\n"


# ── Entry point ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if the files are out of date")
    args = ap.parse_args(argv)
    outputs = {PY_OUT: render_python(), TS_OUT: render_typescript()}
    stale = [p for p, text in outputs.items()
             if not p.exists() or p.read_text(encoding="utf-8").replace("\r\n", "\n") != text]
    if args.check:
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}", file=sys.stderr)
        return 1 if stale else 0
    for p, text in outputs.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

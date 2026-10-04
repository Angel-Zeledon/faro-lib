"""Capture a real, trimmed example response for every read endpoint an API key
can call, into `Frontend/public/api-response-examples.json` (served as a static
file and fetched by /desarrolladores only when someone opens an endpoint).

The developer reference (/desarrolladores) used to show only the generic
`{success, data, meta}` envelope, because no route declares a response model.
This records what each GET actually answers, against a running backend signed
in as a seeded demo account, so an integrator sees real field names and types.

    # backend running locally, demo tenant seeded (backend.scripts.seed_demo)
    backend/.venv/Scripts/python.exe -m backend.scripts.capture_api_examples \
        --base http://127.0.0.1:8011 --email demo@faro.app --password demo1234

Deliberately NOT part of the snapshot check that guards public-api.json: the
examples depend on the data in the account, so they are refreshed by hand
when a response shape changes. Every example is trimmed (two items per list,
long strings cut, depth capped) and scrubbed (emails, phone numbers, tokens).
Write endpoints are never called: only GETs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "Frontend" / "src" / "data" / "public-api.json"
OUT = ROOT / "Frontend" / "public" / "api-response-examples.json"

_MAX_LIST = 1
_MAX_STR = 80
_MAX_DEPTH = 5
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+\d[\d\s-]{7,}\d")
_PHONE_KEYS = re.compile(r"(phone|whatsapp|tel)", re.I)
_SECRET_KEYS = re.compile(r"(token|secret|password|api_key|key_hash|refresh)", re.I)


def _scrub(value):
    if isinstance(value, str):
        v = _EMAIL.sub("user@example.com", value)
        v = _PHONE.sub("+50600000000", v)
        return v if len(v) <= _MAX_STR else v[:_MAX_STR] + "…"
    return value


def _trim(value, depth: int = 0, key: str = ""):
    if key and _SECRET_KEYS.search(key) and isinstance(value, str):
        return "…"
    if key and _PHONE_KEYS.search(key) and isinstance(value, str) and value:
        return "+50600000000"
    if depth >= _MAX_DEPTH:
        return "…" if isinstance(value, (dict, list)) else _scrub(value)
    if isinstance(value, dict):
        return {k: _trim(v, depth + 1, k) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        return [_trim(v, depth + 1, key) for v in value[:_MAX_LIST]]
    return _scrub(value)


def _type_of(value) -> str:
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
        inner = sorted({_type_of(v) for v in value}) or ["unknown"]
        return f"array<{'|'.join(inner)}>"
    return "object"


def _fields(data, prefix: str = "", depth: int = 0, out: list | None = None) -> list:
    """Flatten `data` into dotted field paths with their observed type."""
    out = [] if out is None else out
    if depth > 3 or len(out) > 40:
        return out
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            _fields(data[0], prefix + "[]", depth + 1, out)
        return out
    if isinstance(data, dict):
        for k, v in data.items():
            path = f"{prefix}.{k}" if prefix else k
            out.append({"name": path, "type": _type_of(v)})
            if isinstance(v, (dict, list)):
                _fields(v, path, depth + 1, out)
    return out


def _fill_path(path: str, known: dict) -> str | None:
    def repl(m):
        name = m.group(1)
        return str(known[name]) if name in known else "\0"
    filled = re.sub(r"\{([^}]+)\}", repl, path)
    return None if "\0" in filled else filled


def _harvest(data, known: dict) -> None:
    """Remember ids seen in list responses, to fill path parameters later."""
    items = data if isinstance(data, list) else (
        data.get("items") if isinstance(data, dict) and isinstance(data.get("items"), list) else None)
    if not items:
        return
    first = items[0] if isinstance(items[0], dict) else {}
    for k in ("sku", "session_id", "supplier_id", "po_log_id", "warehouse", "dataset_id", "job_id", "scenario_id"):
        if k in first and k not in known:
            known[k] = first[k]
    if "id" in first:
        known.setdefault("_last_id", first["id"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8011")
    ap.add_argument("--email", default="demo@faro.app")
    ap.add_argument("--password", default="demo1234")
    args = ap.parse_args(argv)

    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    prefix = snapshot["base_path"]
    gets = [e for t in snapshot["tags"] for e in t["endpoints"] if e["method"] == "GET"]

    with httpx.Client(base_url=args.base, timeout=60) as c:
        r = c.post(f"{prefix}/auth/login", json={"email": args.email, "password": args.password})
        r.raise_for_status()
        c.headers["Authorization"] = f"Bearer {r.json()['data']['access_token']}"

        known: dict = {}
        examples: dict = {}
        # Paths without parameters first: they yield the ids the others need.
        for ep in sorted(gets, key=lambda e: "{" in e["path"]):
            path = _fill_path(ep["path"], known | {"id": known.get("_last_id", "")} if "{id}" in ep["path"] else known)
            if path is None:
                continue
            try:
                resp = c.get(prefix + path)
            except httpx.HTTPError:
                continue
            ctype = resp.headers.get("content-type", "")
            if resp.status_code != 200 or "application/json" not in ctype:
                continue
            body = resp.json()
            data = body.get("data", body) if isinstance(body, dict) else body
            _harvest(data, known)
            if isinstance(data, list) and data and isinstance(data[0], dict):
                for k in ("sku", "session_id"):
                    if k in data[0]:
                        known.setdefault(k, data[0][k])
            if isinstance(data, dict):
                for k in ("session_id", "active_session_id"):
                    if isinstance(data.get(k), str):
                        known.setdefault("session_id", data[k])
            trimmed = _trim(body)
            examples[ep["id"]] = {
                "example": trimmed,
                "fields": _fields(data if isinstance(body, dict) and "data" in body else body),
            }

    OUT.write_text(json.dumps(examples, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"captured {len(examples)} of {len(gets)} GET endpoints -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

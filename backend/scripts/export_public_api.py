"""Export the API-key surface to `Frontend/src/data/public-api.json`.

The developer reference on the landing (/desarrolladores) is rendered from that
file at build time. It is generated from the running app's OpenAPI document —
only the operations `backend/api/public_surface.py` exposes to an API key — so
the reference and the API cannot describe two different things:

    backend/.venv/Scripts/python.exe -m backend.scripts.export_public_api
    backend/.venv/Scripts/python.exe -m backend.scripts.export_public_api --check

`--check` regenerates into memory and compares, exiting non-zero on a
difference. `backend/tests/test_public_api_snapshot.py` runs exactly that: a
route added to an exposed area, or one whose parameters changed, turns the
suite red until the snapshot is regenerated — which is the moment somebody
looks at the diff and decides the new route belongs on a public page.

The output is deterministic (sorted, no timestamps, no environment values) so
two runs on the same code produce the same bytes.

What is in an endpoint entry:

* `parameters[]` and `request_body.schema` carry a SCHEMA TREE (see `_node`),
  not a type label: arrays, objects and maps stay distinct all the way down, so
  a page can never present an object as a list item or the reverse. A request
  body that is itself a list (`PATCH /sessions/{id}/overrides`) has an `array`
  root.
* `request_body.example` is built from that tree (or hand-written in
  `backend/api/public_examples.py` where the schema is a free-form object).
* `responses[]` lists the non-success statuses the route can answer.

Response bodies are NOT here: no route declares a response model, so there is
nothing to export. `capture_api_examples.py` records what each endpoint really
answers into `Frontend/public/api-response-examples.json`, and a test compares
that file with the live routes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "Frontend" / "src" / "data" / "public-api.json"

# Operations an API key reaches but that only ever answer an error: listing
# them as callable would document a request that cannot succeed.
# `GET /mcp` answers 405 on purpose (the server is stateless and offers no
# server-to-client stream); the MCP endpoint is POST /mcp.
_NOT_CALLABLE = {("GET", "/mcp")}

# How deep a schema tree is expanded before a nested object is shown as an
# opaque `object`. Cycles are cut earlier, by name.
_MAX_DEPTH = 6


def _resolve(schema: dict, components: dict) -> dict:
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 10:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = components.get(name, {})
        seen += 1
    return schema or {}


def _ref_name(schema: dict) -> str | None:
    if isinstance(schema, dict) and "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return None


def _is_file(schema: dict) -> bool:
    # OpenAPI 3.1 (FastAPI >= 0.100) spells an upload `contentMediaType`; 3.0
    # spelled it `format: binary`.
    return schema.get("type") == "string" and (
        schema.get("format") == "binary" or "contentMediaType" in schema)


def _first_paragraph(text: str | None) -> str:
    if not text:
        return ""
    para = text.strip().split("\n\n", 1)[0]
    out = ""
    for line in (ln.strip() for ln in para.splitlines()):
        # A list item starts its own line; everything else flows as one paragraph.
        out += ("\n" if line.startswith("- ") and out else " " if out else "") + line
    return out.strip()


def _node(schema: dict, components: dict, depth: int = 0, trail: tuple = ()) -> dict:
    """A schema as a small, renderer-friendly tree.

    Keys (all optional except `type`): `nullable`, `format`, `enum`, `default`,
    `minimum`/`maximum`/`min_length`/`max_length`, `description`, and by type
    `items` (array), `fields` (object with declared properties, each a node
    plus `name` and `required`), `values` (object used as a map) or
    `free_form` (an object that declares no properties at all), `any_of`
    (a union of several non-null shapes).
    """
    ref = _ref_name(schema)
    resolved = _resolve(schema, components)
    nullable = False

    if ref and ref in trail:
        return {"type": "object", "ref": ref}
    if depth > _MAX_DEPTH:
        return {"type": "object"}
    trail = trail + ((ref,) if ref else ())

    for key in ("anyOf", "oneOf"):
        if key in resolved:
            options = []
            for option in resolved[key]:
                if _resolve(option, components).get("type") == "null":
                    nullable = True
                else:
                    options.append(option)
            if not options:
                return {"type": "null"}
            if len(options) == 1:
                node = _node(options[0], components, depth, trail)
            else:
                branches = [_node(o, components, depth + 1, trail) for o in options]
                node = {"type": " | ".join(dict.fromkeys(b["type"] for b in branches)),
                        "any_of": branches}
            if nullable:
                node["nullable"] = True
            desc = _first_paragraph(resolved.get("description"))
            if desc and "description" not in node:
                node["description"] = desc
            if resolved.get("default") is not None and "default" not in node:
                node["default"] = resolved["default"]
            return node
    if "allOf" in resolved and len(resolved["allOf"]) == 1:
        node = _node(resolved["allOf"][0], components, depth, trail)
        desc = _first_paragraph(resolved.get("description"))
        if desc:
            node["description"] = desc
        return node

    t = resolved.get("type")
    if isinstance(t, list):  # OpenAPI 3.1 may spell Optional as ["string", "null"]
        nullable = "null" in t
        t = next((x for x in t if x != "null"), None)
    node: dict = {}
    if _is_file(resolved):
        node["type"] = "file"
    elif t == "array":
        node["type"] = "array"
        node["items"] = _node(resolved.get("items", {}), components, depth + 1, trail)
    elif t == "object" or "properties" in resolved:
        node["type"] = "object"
        props = resolved.get("properties", {})
        required = set(resolved.get("required", []))
        if props:
            node["fields"] = [
                {"name": k, "required": k in required, **_node(v, components, depth + 1, trail)}
                for k, v in props.items()
            ]
        extra = resolved.get("additionalProperties")
        if isinstance(extra, dict) and extra:
            node["values"] = _node(extra, components, depth + 1, trail)
        elif not props:
            node["free_form"] = True
    elif t:
        node["type"] = str(t)
        if resolved.get("format"):
            node["format"] = resolved["format"]
    elif "enum" in resolved:
        node["type"] = "string"
    else:
        node["type"] = "any"
    if "enum" in resolved:
        node["enum"] = list(resolved["enum"])
    if "const" in resolved:
        node["enum"] = [resolved["const"]]
    for src, dst in (("minimum", "minimum"), ("maximum", "maximum"),
                     ("exclusiveMinimum", "exclusive_minimum"),
                     ("minLength", "min_length"), ("maxLength", "max_length")):
        if src in resolved:
            node[dst] = resolved[src]
    if resolved.get("default") is not None:
        node["default"] = resolved["default"]
    desc = _first_paragraph(resolved.get("description"))
    if desc:
        node["description"] = desc
    if nullable:
        node["nullable"] = True
    return node


def _string_example(name: str) -> str:
    n = name.lower()
    if n == "sku" or n.endswith("_sku"):
        return "SKU-001"
    if n.endswith("id"):
        return f"<{name}>"
    if "email" in n:
        return "buyer@example.com"
    if "phone" in n or "whatsapp" in n:
        return "+50688887777"
    if n.endswith("date") or n == "date" or n.startswith("date_"):
        return "2026-10-01"
    if n in ("month", "period"):
        return "2026-10"
    if n.endswith("_at") or n.endswith("timestamp"):
        return "2026-10-01T08:00:00Z"
    if n in ("warehouse", "location") or n.endswith("_warehouse"):
        return "principal"
    if n in ("currency", "currency_code"):
        return "CRC"
    if n == "url" or n.endswith("_url"):
        return "https://example.com/hooks/stockai"
    if n in ("reason", "notes", "note", "comment", "description"):
        return "Free-text note"
    if n in ("name", "display_name", "title"):
        return "Example name"
    if n == "supplier" or n.endswith("_supplier"):
        return "Distribuidora Andina"
    if n in ("category", "family", "brand"):
        return "Pantry"
    if n in ("query", "sql") or n.endswith("_query"):
        return "SELECT sku, date, quantity FROM sales"
    if n == "host":
        return "db.example.com"
    if n == "database":
        return "erp"
    if n == "username":
        return "readonly"
    if n == "password":
        return "<password>"
    return name or "string"


def _example(node: dict, name: str = "", depth: int = 0):
    """A plausible value for a node, from its declared constraints."""
    if node.get("enum"):
        return node["enum"][0]
    t = node["type"]
    default = node.get("default")
    if default is not None and (t not in ("object", "array") or default):
        return default
    if "any_of" in node:
        return _example(node["any_of"][0], name, depth)
    if t == "object":
        if depth > _MAX_DEPTH:
            return {}
        fields = node.get("fields")
        if fields is not None:
            # Required fields always; optional ones too while the object is
            # small, so an example shows what CAN be sent without becoming a wall.
            chosen = fields if len(fields) <= 8 else [f for f in fields if f["required"]]
            return {f["name"]: _example(f, f["name"], depth + 1) for f in chosen}
        if "values" in node:
            return {"key": _example(node["values"], name, depth + 1)}
        return {}
    if t == "array":
        return [_example(node.get("items", {"type": "any"}), name, depth + 1)]
    if t in ("integer", "number"):
        if "minimum" in node:
            base = node["minimum"]
        elif "exclusive_minimum" in node:
            base = node["exclusive_minimum"] + 1
        else:
            base = 1
        base = base or 1
        return int(base) if t == "integer" else float(base)
    if t == "boolean":
        return False
    if t == "file":
        return "@sales.csv"
    if t == "string":
        fmt = node.get("format")
        if fmt == "date":
            return "2026-10-01"
        if fmt == "date-time":
            return "2026-10-01T08:00:00Z"
        if fmt == "email":
            return "buyer@example.com"
        text = _string_example(name)
        if node.get("min_length") and len(text) < node["min_length"]:
            text = text.ljust(node["min_length"], "x")
        return text
    return None


def build(app=None) -> dict:
    """The snapshot as a dict. `app` defaults to the real one."""
    if app is None:
        from backend.main import app as _app
        app = _app
    from backend.api.public_examples import BODY_EXAMPLES, BODY_FIELDS
    from backend.api.public_surface import API_PREFIX, exposure
    from backend.auth.api_key_auth import RATE_MAX_PER_MINUTE, RATE_WINDOW_SECONDS
    from backend.entitlements.plans import PLANS

    schema = app.openapi()
    components = schema.get("components", {}).get("schemas", {})

    endpoints: list[dict] = []
    for route in app.routes:
        exp = exposure(route)
        if not exp.exposed:
            continue
        item = schema.get("paths", {}).get(route.path_format, {})
        for method in sorted(route.methods or ()):
            op = item.get(method.lower())
            if op is None:
                continue
            path = route.path_format[len(API_PREFIX):]
            if (method.upper(), path) in _NOT_CALLABLE:
                continue
            params = []
            for p in op.get("parameters", []):
                pnode = _node(p.get("schema", {}), components)
                # A query or header value cannot be sent as null: "optional" says it all.
                pnode.pop("nullable", None)
                if p.get("description") and "description" not in pnode:
                    pnode["description"] = _first_paragraph(p["description"])
                # `Query(..., description="a | b | c")` is how several routes
                # state an enumeration the schema cannot: lift it into `enum`.
                words = pnode.get("description", "")
                if "enum" not in pnode and pnode["type"] == "string" and re.fullmatch(r"\w+( \| \w+)+", words):
                    pnode["enum"] = words.split(" | ")
                example = _example(pnode, p["name"])
                if p["in"] in ("path", "query") and isinstance(example, str) and example == p["name"]:
                    example = f"<{p['name']}>"
                params.append({
                    "name": p["name"],
                    "in": p["in"],
                    "required": bool(p.get("required")),
                    "schema": pnode,
                    "example": example,
                })
            body = None
            rb = op.get("requestBody")
            if rb is None and (method.upper(), path) in BODY_FIELDS:
                # Reads its body from the raw request (JSON-RPC): OpenAPI has
                # no requestBody at all, so the entry in public_examples.py is
                # the only description there is.
                rb = {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}
            if rb:
                content = rb.get("content", {})
                ctype = "application/json" if "application/json" in content else sorted(content)[0]
                bnode = _node(content[ctype].get("schema", {}), components)
                if (method.upper(), path) in BODY_FIELDS:
                    # A route that reads keys out of a bare dict: OpenAPI can
                    # only say "object", so the fields are written by hand.
                    bnode = {"type": "object", "fields": BODY_FIELDS[(method.upper(), path)]}
                body = {
                    "content_type": ctype,
                    "required": bool(rb.get("required")),
                    "schema": bnode,
                    "example": BODY_EXAMPLES.get((method.upper(), path), _example(bnode)),
                }
            ok = op.get("responses", {})
            success = next((c for c in sorted(ok) if str(c).startswith("2")), "200")
            content_types = sorted(ok.get(success, {}).get("content", {}).keys())
            errors = [
                {"status": int(code), "description": _first_paragraph(r.get("description"))}
                for code, r in sorted(ok.items()) if str(code).isdigit() and not str(code).startswith("2")
            ]
            endpoints.append({
                "id": f"{method.lower()}-{path.strip('/').replace('/', '-').replace('{', '').replace('}', '')}",
                "method": method.upper(),
                "path": path,
                "tag": (route.tags or ["other"])[0],
                "scope": exp.scope,
                "summary": op.get("summary") or route.name,
                "description": _first_paragraph(op.get("description")),
                "parameters": params,
                "request_body": body,
                "success_status": int(success) if str(success).isdigit() else 200,
                "response_content_types": content_types or ["application/json"],
                "errors": errors,
            })

    endpoints.sort(key=lambda e: (e["tag"], e["path"], e["method"]))
    tags: dict[str, list[dict]] = {}
    for e in endpoints:
        tags.setdefault(e["tag"], []).append(e)

    return {
        "generated_by": "backend/scripts/export_public_api.py",
        "base_path": API_PREFIX,
        "counts": {
            "total": len(endpoints),
            "read": sum(1 for e in endpoints if e["scope"] == "read"),
            "write": sum(1 for e in endpoints if e["scope"] == "write"),
        },
        "limits": {
            "per_minute_per_key": RATE_MAX_PER_MINUTE,
            "window_seconds": RATE_WINDOW_SECONDS,
            "per_day_per_key": {
                tier: PLANS[tier].max_api_calls_per_day
                for tier in ("demo", "free", "paid") if tier in PLANS
            },
        },
        "tags": [{"tag": t, "endpoints": eps} for t, eps in sorted(tags.items())],
    }


def render(app=None) -> str:
    return json.dumps(build(app), indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the committed snapshot is stale")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
        if current != text:
            print(f"{SNAPSHOT} is stale: run python -m backend.scripts.export_public_api",
                  file=sys.stderr)
            return 1
        print("public-api.json is up to date")
        return 0
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

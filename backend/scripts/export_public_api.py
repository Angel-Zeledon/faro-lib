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
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "Frontend" / "src" / "data" / "public-api.json"

# How deep an example body is expanded before nested objects become null.
_MAX_DEPTH = 5


def _resolve(schema: dict, components: dict) -> dict:
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 10:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = components.get(name, {})
        seen += 1
    return schema or {}


def _is_file(schema: dict) -> bool:
    # OpenAPI 3.1 (FastAPI ≥ 0.100) spells an upload `contentMediaType`; 3.0
    # spelled it `format: binary`.
    return schema.get("type") == "string" and (
        schema.get("format") == "binary" or "contentMediaType" in schema)


def _non_null(schema: dict, components: dict) -> dict:
    """The first non-null branch of an Optional (anyOf [X, null])."""
    schema = _resolve(schema, components)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            for option in schema[key]:
                resolved = _resolve(option, components)
                if resolved.get("type") != "null":
                    return resolved
    return schema


def _type_label(schema: dict, components: dict) -> str:
    schema = _resolve(schema, components)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            parts = [_type_label(s, components) for s in schema[key]]
            parts = [p for p in parts if p != "null"]
            return " | ".join(dict.fromkeys(parts)) or "any"
    t = schema.get("type")
    if t == "array":
        return f"array<{_type_label(schema.get('items', {}), components)}>"
    if "enum" in schema:
        return "enum"
    if _is_file(schema):
        return "file"
    if t == "string" and schema.get("format"):
        return f"string({schema['format']})"
    if t:
        return str(t)
    if "properties" in schema:
        return "object"
    return "any"


def _example(schema: dict, components: dict, name: str = "", depth: int = 0):
    schema = _resolve(schema, components)
    if "example" in schema:
        return schema["example"]
    if schema.get("examples"):
        ex = schema["examples"]
        return ex[0] if isinstance(ex, list) else ex
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [s for s in schema[key] if _resolve(s, components).get("type") != "null"]
            if options:
                return _example(options[0], components, name, depth)
            return None
    if depth > _MAX_DEPTH:
        return None
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        # Required fields always; optional ones too while the object is small,
        # so an example shows what CAN be sent without becoming a wall.
        keys = list(props) if len(props) <= 8 else [k for k in props if k in required]
        return {k: _example(props[k], components, k, depth + 1) for k in keys}
    if t == "array":
        return [_example(schema.get("items", {}), components, name, depth + 1)]
    if t == "integer":
        return int(schema.get("minimum", 1) or 1)
    if t == "number":
        return float(schema.get("minimum", 1) or 1)
    if t == "boolean":
        return False
    if t == "string":
        fmt = schema.get("format")
        if fmt == "date":
            return "2026-10-01"
        if fmt == "date-time":
            return "2026-10-01T08:00:00Z"
        if _is_file(schema):
            return "@sales.csv"
        lowered = name.lower()
        if lowered == "sku" or lowered.endswith("_sku"):
            return "SKU-001"
        if lowered.endswith("id"):
            return f"<{name}>"
        return name or "string"
    return None


def _first_paragraph(text: str | None) -> str:
    if not text:
        return ""
    para = text.strip().split("\n\n", 1)[0]
    return " ".join(line.strip() for line in para.splitlines()).strip()


def _fields(schema: dict, components: dict) -> list[dict]:
    """Top-level fields of a body. An Optional body is unwrapped; a list body
    (e.g. forecast overrides) documents the fields of one item."""
    schema = _non_null(schema, components)
    if schema.get("type") == "array":
        schema = _non_null(schema.get("items", {}), components)
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    out = []
    for key, prop in props.items():
        resolved = _resolve(prop, components)
        out.append({
            "name": key,
            "type": _type_label(prop, components),
            "required": key in required,
            "description": _first_paragraph(resolved.get("description") or prop.get("description")),
        })
    return out


def build(app=None) -> dict:
    """The snapshot as a dict. `app` defaults to the real one."""
    if app is None:
        from backend.main import app as _app
        app = _app
    from backend.api.public_surface import API_PREFIX, exposure
    from backend.auth.api_key_auth import RATE_MAX_PER_MINUTE
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
            params = []
            for p in op.get("parameters", []):
                pschema = p.get("schema", {})
                params.append({
                    "name": p["name"],
                    "in": p["in"],
                    "required": bool(p.get("required")),
                    "type": _type_label(pschema, components),
                    "description": _first_paragraph(
                        p.get("description") or _resolve(pschema, components).get("description")),
                    "example": _example(pschema, components, p["name"]),
                })
            body = None
            rb = op.get("requestBody")
            if rb:
                content = rb.get("content", {})
                ctype = "application/json" if "application/json" in content else sorted(content)[0]
                bschema = content[ctype].get("schema", {})
                body = {
                    "content_type": ctype,
                    "required": bool(rb.get("required")),
                    "fields": _fields(bschema, components),
                    "example": _example(bschema, components),
                }
            ok = op.get("responses", {})
            success = next((c for c in sorted(ok) if str(c).startswith("2")), "200")
            content_types = sorted(ok.get(success, {}).get("content", {}).keys())
            path = route.path_format[len(API_PREFIX):]
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
            "per_day_per_key": {
                tier: PLANS[tier].max_api_calls_per_day for tier in ("free", "paid") if tier in PLANS
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
